from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import sqlite3, json, os, datetime, requests, hashlib, secrets
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB = "notify.db"

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT UNIQUE,
        tags TEXT DEFAULT '[]',
        meta TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        body TEXT,
        variables TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        template_id INTEGER,
        status TEXT DEFAULT 'draft',
        sent INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT,
        phone TEXT,
        body TEXT,
        status TEXT DEFAULT 'pending',
        campaign_id INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS webhooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        url TEXT,
        secret TEXT,
        events TEXT DEFAULT '[]',
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS field_mappings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        source_field TEXT,
        target_label TEXT,
        transform TEXT DEFAULT 'none',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        instructions TEXT,
        trigger_keywords TEXT DEFAULT '[]',
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT DEFAULT 'admin'
    );
    """)
    # Default admin
    pw = hashlib.sha256("admin123".encode()).hexdigest()
    db.execute("INSERT OR IGNORE INTO users (username,password,role) VALUES (?,?,?)",
               ("admin", pw, "admin"))
    # Default settings
    defaults = {
        "green_api_instance": "", "green_api_token": "",
        "ai_provider": "claude", "ai_model": "claude-sonnet-4-20250514",
        "anthropic_api_key": "", "app_name": "NotifyHub",
        "app_tagline": "Smart WhatsApp Notifications", "timezone": "Africa/Johannesburg"
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
    db.commit()
    db.close()

def get_setting(key, default=""):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    db.close()
    return row["value"] if row else default

def set_setting(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    db.commit()
    db.close()

# ── Auth ─────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","")
        password = hashlib.sha256(request.form.get("password","").encode()).hexdigest()
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=? AND password=?",
                          (username, password)).fetchone()
        db.close()
        if user:
            session["user"] = dict(user)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "error")
    return render_template("login.html", app_name=get_setting("app_name","NotifyHub"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Dashboard ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    db = get_db()
    stats = {
        "contacts": db.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"],
        "sent":     db.execute("SELECT COUNT(*) as c FROM messages WHERE direction='out'").fetchone()["c"],
        "received": db.execute("SELECT COUNT(*) as c FROM messages WHERE direction='in'").fetchone()["c"],
        "campaigns":db.execute("SELECT COUNT(*) as c FROM campaigns").fetchone()["c"],
    }
    recent = db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 10").fetchall()
    db.close()
    return render_template("dashboard.html", stats=stats, recent=recent,
                           app_name=get_setting("app_name","NotifyHub"))

# ── Contacts ─────────────────────────────────────────────────────────────────
@app.route("/contacts")
@login_required
def contacts():
    db = get_db()
    rows = db.execute("SELECT * FROM contacts ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("contacts.html", contacts=rows,
                           app_name=get_setting("app_name","NotifyHub"))

@app.route("/contacts/add", methods=["POST"])
@login_required
def add_contact():
    db = get_db()
    try:
        db.execute("INSERT INTO contacts (name,phone,tags,meta) VALUES (?,?,?,?)",
                   (request.form["name"], request.form["phone"],
                    request.form.get("tags","[]"), request.form.get("meta","{}")))
        db.commit()
        flash("Contact added", "success")
    except Exception as e:
        flash(str(e), "error")
    db.close()
    return redirect(url_for("contacts"))

@app.route("/contacts/<int:cid>/delete", methods=["POST"])
@login_required
def delete_contact(cid):
    db = get_db()
    db.execute("DELETE FROM contacts WHERE id=?", (cid,))
    db.commit()
    db.close()
    flash("Contact deleted", "success")
    return redirect(url_for("contacts"))

# ── Templates ─────────────────────────────────────────────────────────────────
@app.route("/templates")
@login_required
def templates():
    db = get_db()
    rows = db.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("templates.html", templates=rows,
                           app_name=get_setting("app_name","NotifyHub"))

@app.route("/templates/add", methods=["POST"])
@login_required
def add_template():
    db = get_db()
    body = request.form["body"]
    import re
    vars_ = json.dumps(list(set(re.findall(r'\{\{(\w+)\}\}', body))))
    db.execute("INSERT INTO templates (name,body,variables) VALUES (?,?,?)",
               (request.form["name"], body, vars_))
    db.commit()
    db.close()
    flash("Template saved", "success")
    return redirect(url_for("templates"))

@app.route("/templates/<int:tid>/delete", methods=["POST"])
@login_required
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM templates WHERE id=?", (tid,))
    db.commit()
    db.close()
    flash("Template deleted", "success")
    return redirect(url_for("templates"))

# ── Send Message ─────────────────────────────────────────────────────────────
def send_whatsapp(phone, message):
    instance = get_setting("green_api_instance")
    token    = get_setting("green_api_token")
    if not instance or not token:
        return False, "Green API not configured"
    url = f"https://api.green-api.com/waInstance{instance}/sendMessage/{token}"
    phone_id = phone.replace("+","").replace(" ","") + "@c.us"
    try:
        r = requests.post(url, json={"chatId": phone_id, "message": message}, timeout=15)
        return r.status_code == 200, r.text
    except Exception as e:
        return False, str(e)

@app.route("/send", methods=["GET","POST"])
@login_required
def send():
    db = get_db()
    templates_list = db.execute("SELECT * FROM templates").fetchall()
    contacts_list  = db.execute("SELECT * FROM contacts ORDER BY name").fetchall()
    if request.method == "POST":
        phones  = request.form.getlist("phones")
        message = request.form["message"]
        results = []
        for phone in phones:
            ok, resp = send_whatsapp(phone, message)
            status = "sent" if ok else "failed"
            db.execute("INSERT INTO messages (direction,phone,body,status) VALUES (?,?,?,?)",
                       ("out", phone, message, status))
            results.append({"phone": phone, "ok": ok, "resp": resp})
        db.commit()
        db.close()
        flash(f"Sent to {len([r for r in results if r['ok']])} / {len(results)} contacts", "success")
        return redirect(url_for("send"))
    db.close()
    return render_template("send.html", templates=templates_list, contacts=contacts_list,
                           app_name=get_setting("app_name","NotifyHub"))

# ── Campaigns ─────────────────────────────────────────────────────────────────
@app.route("/campaigns")
@login_required
def campaigns():
    db = get_db()
    rows = db.execute("""SELECT c.*, t.name as template_name 
                         FROM campaigns c LEFT JOIN templates t ON c.template_id=t.id
                         ORDER BY c.created_at DESC""").fetchall()
    db.close()
    return render_template("campaigns.html", campaigns=rows,
                           app_name=get_setting("app_name","NotifyHub"))

# ── Messages Log ─────────────────────────────────────────────────────────────
@app.route("/messages")
@login_required
def messages():
    db = get_db()
    rows = db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 200").fetchall()
    db.close()
    return render_template("messages.html", messages=rows,
                           app_name=get_setting("app_name","NotifyHub"))

# ── Field Mapper ─────────────────────────────────────────────────────────────
@app.route("/mapper")
@login_required
def mapper():
    db = get_db()
    mappings = db.execute("SELECT * FROM field_mappings ORDER BY created_at DESC").fetchall()
    templates_list = db.execute("SELECT * FROM templates").fetchall()
    db.close()
    return render_template("mapper.html", mappings=mappings, templates=templates_list,
                           app_name=get_setting("app_name","NotifyHub"))

@app.route("/mapper/add", methods=["POST"])
@login_required
def add_mapping():
    db = get_db()
    db.execute("INSERT INTO field_mappings (name,source_field,target_label,transform) VALUES (?,?,?,?)",
               (request.form["name"], request.form["source_field"],
                request.form["target_label"], request.form.get("transform","none")))
    db.commit()
    db.close()
    flash("Mapping saved", "success")
    return redirect(url_for("mapper"))

@app.route("/mapper/<int:mid>/delete", methods=["POST"])
@login_required
def delete_mapping(mid):
    db = get_db()
    db.execute("DELETE FROM field_mappings WHERE id=?", (mid,))
    db.commit()
    db.close()
    return redirect(url_for("mapper"))

# ── AI Agents ─────────────────────────────────────────────────────────────────
@app.route("/agents")
@login_required
def agents():
    db = get_db()
    rows = db.execute("SELECT * FROM ai_agents ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("agents.html", agents=rows,
                           app_name=get_setting("app_name","NotifyHub"))

@app.route("/agents/add", methods=["POST"])
@login_required
def add_agent():
    db = get_db()
    keywords = json.dumps([k.strip() for k in request.form.get("trigger_keywords","").split(",")])
    db.execute("INSERT INTO ai_agents (name,instructions,trigger_keywords,active) VALUES (?,?,?,?)",
               (request.form["name"], request.form["instructions"], keywords,
                1 if request.form.get("active") else 0))
    db.commit()
    db.close()
    flash("Agent created", "success")
    return redirect(url_for("agents"))

@app.route("/agents/<int:aid>/delete", methods=["POST"])
@login_required
def delete_agent(aid):
    db = get_db()
    db.execute("DELETE FROM ai_agents WHERE id=?", (aid,))
    db.commit()
    db.close()
    return redirect(url_for("agents"))

@app.route("/agents/<int:aid>/toggle", methods=["POST"])
@login_required
def toggle_agent(aid):
    db = get_db()
    current = db.execute("SELECT active FROM ai_agents WHERE id=?", (aid,)).fetchone()
    if current:
        db.execute("UPDATE ai_agents SET active=? WHERE id=?", (0 if current["active"] else 1, aid))
        db.commit()
    db.close()
    return redirect(url_for("agents"))

# ── Webhooks ──────────────────────────────────────────────────────────────────
@app.route("/webhooks")
@login_required
def webhooks():
    db = get_db()
    rows = db.execute("SELECT * FROM webhooks ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("webhooks.html", webhooks=rows,
                           app_name=get_setting("app_name","NotifyHub"))

@app.route("/webhooks/add", methods=["POST"])
@login_required
def add_webhook():
    db = get_db()
    events = json.dumps(request.form.getlist("events"))
    sec = secrets.token_hex(16)
    db.execute("INSERT INTO webhooks (name,url,secret,events,active) VALUES (?,?,?,?,?)",
               (request.form["name"], request.form["url"], sec, events, 1))
    db.commit()
    db.close()
    flash("Webhook added", "success")
    return redirect(url_for("webhooks"))

@app.route("/webhooks/<int:wid>/delete", methods=["POST"])
@login_required
def delete_webhook(wid):
    db = get_db()
    db.execute("DELETE FROM webhooks WHERE id=?", (wid,))
    db.commit()
    db.close()
    return redirect(url_for("webhooks"))

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    keys = ["green_api_instance","green_api_token","ai_provider","ai_model",
            "anthropic_api_key","app_name","app_tagline","timezone"]
    if request.method == "POST":
        for k in keys:
            if k in request.form:
                set_setting(k, request.form[k])
        flash("Settings saved", "success")
        return redirect(url_for("settings"))
    current = {k: get_setting(k) for k in keys}
    return render_template("settings.html", settings=current,
                           app_name=get_setting("app_name","NotifyHub"))

# ── Incoming Webhook (Green API) ──────────────────────────────────────────────
@app.route("/api/incoming", methods=["POST"])
def incoming():
    data = request.get_json(silent=True) or {}
    # Green API webhook format
    msg_data = data.get("messageData", {})
    sender   = data.get("senderData", {})
    phone    = sender.get("sender","").replace("@c.us","")
    body     = msg_data.get("textMessageData", {}).get("textMessage","") or \
               msg_data.get("extendedTextMessageData", {}).get("text","")
    if not phone or not body:
        return jsonify({"ok": True})

    db = get_db()
    db.execute("INSERT INTO messages (direction,phone,body,status) VALUES (?,?,?,?)",
               ("in", phone, body, "received"))
    db.commit()

    # Check AI agents
    agents = db.execute("SELECT * FROM ai_agents WHERE active=1").fetchall()
    for agent in agents:
        keywords = json.loads(agent["trigger_keywords"] or "[]")
        matched = not keywords or any(kw.lower() in body.lower() for kw in keywords)
        if matched:
            ai_reply = call_ai_agent(agent["instructions"], body, phone)
            if ai_reply:
                ok, _ = send_whatsapp(phone, ai_reply)
                db.execute("INSERT INTO messages (direction,phone,body,status) VALUES (?,?,?,?)",
                           ("out", phone, ai_reply, "sent" if ok else "failed"))
                db.commit()
            break
    db.close()
    return jsonify({"ok": True})

def call_ai_agent(instructions, user_message, phone):
    api_key = get_setting("anthropic_api_key")
    if not api_key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={
                "model": get_setting("ai_model","claude-sonnet-4-20250514"),
                "max_tokens": 500,
                "system": instructions,
                "messages": [{"role":"user","content": user_message}]
            }, timeout=20
        )
        data = r.json()
        return data.get("content",[{}])[0].get("text","")
    except:
        return None

# ── REST API for External Platforms ──────────────────────────────────────────
@app.route("/api/send", methods=["POST"])
def api_send():
    auth = request.headers.get("X-API-Key","")
    # Simple API key check (use setting)
    api_key = get_setting("api_key","")
    if api_key and auth != api_key:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    phone   = data.get("phone","")
    message = data.get("message","")
    template_id = data.get("template_id")
    variables   = data.get("variables",{})

    if not phone:
        return jsonify({"error": "phone required"}), 400

    if template_id:
        db = get_db()
        t = db.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        db.close()
        if t:
            message = t["body"]
            for k, v in variables.items():
                message = message.replace(f"{{{{{k}}}}}", str(v))

    if not message:
        return jsonify({"error": "message required"}), 400

    ok, resp = send_whatsapp(phone, message)
    db = get_db()
    db.execute("INSERT INTO messages (direction,phone,body,status) VALUES (?,?,?,?)",
               ("out", phone, message, "sent" if ok else "failed"))
    db.commit()
    db.close()
    return jsonify({"ok": ok, "response": resp})

@app.route("/api/contacts", methods=["GET","POST"])
def api_contacts():
    auth = request.headers.get("X-API-Key","")
    api_key = get_setting("api_key","")
    if api_key and auth != api_key:
        return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            db.execute("INSERT OR REPLACE INTO contacts (name,phone,tags,meta) VALUES (?,?,?,?)",
                       (data.get("name",""), data.get("phone",""),
                        json.dumps(data.get("tags",[])), json.dumps(data.get("meta",{}))))
            db.commit()
            db.close()
            return jsonify({"ok": True})
        except Exception as e:
            db.close()
            return jsonify({"error": str(e)}), 400
    rows = db.execute("SELECT * FROM contacts").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/templates", methods=["GET"])
def api_templates():
    db = get_db()
    rows = db.execute("SELECT id,name,variables FROM templates").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/generate-key", methods=["POST"])
@login_required
def generate_api_key():
    key = secrets.token_hex(24)
    set_setting("api_key", key)
    return jsonify({"key": key})

@app.route("/api/stats", methods=["GET"])
@login_required
def api_stats():
    db = get_db()
    stats = {
        "contacts": db.execute("SELECT COUNT(*) as c FROM contacts").fetchone()["c"],
        "sent":     db.execute("SELECT COUNT(*) as c FROM messages WHERE direction='out'").fetchone()["c"],
        "received": db.execute("SELECT COUNT(*) as c FROM messages WHERE direction='in'").fetchone()["c"],
    }
    db.close()
    return jsonify(stats)

@app.template_filter('from_json')
def from_json_filter(s):
    try:
        return json.loads(s or '[]')
    except:
        return []

@app.route("/contacts/import", methods=["POST"])
@login_required
def import_contacts():
    import csv, io
    file = request.files.get("csv_file")
    if not file:
        flash("No file uploaded", "error")
        return redirect(url_for("contacts"))
    content = file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    db = get_db()
    added = 0
    for row in reader:
        name = row.get("name","").strip()
        phone = row.get("phone","").strip()
        tags = row.get("tags","[]").strip()
        if phone:
            try:
                db.execute("INSERT OR IGNORE INTO contacts (name,phone,tags) VALUES (?,?,?)",
                           (name, phone, tags if tags.startswith('[') else json.dumps([tags])))
                added += 1
            except: pass
    db.commit()
    db.close()
    flash(f"Imported {added} contacts", "success")
    return redirect(url_for("contacts"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
