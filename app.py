from flask import Flask, request, jsonify, render_template, redirect, url_for, Response
import sqlite3
import imaplib, email
import google.generativeai as genai

app = Flask(__name__)

# 🔑 Gemini API Key
genai.configure(api_key="your_api_key")
model = genai.GenerativeModel("gemini-1.5-flash")

# 🔑 Gmail IMAP
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
EMAIL_ACCOUNT = "your_gmail"   # change to your Gmail
APP_PASSWORD = "your_password"      # paste Gmail App Password here


# -------- AI Functions --------
def classify_email(content):
    prompt = f"Classify this email into one of [Interested, Not Interested, Ask Info, Spam]:\n\n{content}"
    response = model.generate_content(prompt)
    return response.text.strip()


def summarize_email(content):
    prompt = f"Summarize the main purpose of this email in one short sentence:\n\n{content}"
    response = model.generate_content(prompt)
    return response.text.strip()


def generate_reply(content, intent):
    prompt = f"Email: {content}\nIntent: {intent}\nDraft a polite professional reply email."
    response = model.generate_content(prompt)
    return response.text.strip()


def analyze_sentiment(content):
    """Return Positive, Negative, or Neutral sentiment"""
    prompt = f"Analyze sentiment of this email in one word [Positive, Negative, Neutral]:\n\n{content}"
    response = model.generate_content(prompt)
    return response.text.strip()


# -------- Gmail Fetch --------
def fetch_emails(limit=5):
    """Fetch latest N emails from Gmail via IMAP"""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(EMAIL_ACCOUNT, APP_PASSWORD)
    mail.select("inbox")

    status, data = mail.search(None, "ALL")
    email_ids = data[0].split()
    latest_ids = email_ids[-limit:]

    emails = []
    for eid in latest_ids:
        status, msg_data = mail.fetch(eid, "(RFC822)")
        raw_msg = msg_data[0][1]
        msg = email.message_from_bytes(raw_msg)

        subject = msg["subject"] or "(No Subject)"
        from_ = msg["from"]

        # Get body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        emails.append({
            "subject": subject,
            "from": from_,
            "body": body
        })

    mail.logout()
    return emails


# -------- Routes --------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        email_text = request.form["email"]

        intent = classify_email(email_text)
        purpose = summarize_email(email_text)
        reply = generate_reply(email_text, intent)
        sentiment = analyze_sentiment(email_text)

        # Save to DB
        conn = sqlite3.connect("database.db")
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS emails (
                        id INTEGER PRIMARY KEY,
                        email TEXT,
                        intent TEXT,
                        reply TEXT,
                        purpose TEXT,
                        sentiment TEXT,
                        important INTEGER DEFAULT 0
                    )""")
        c.execute("INSERT INTO emails (email, intent, reply, purpose, sentiment) VALUES (?, ?, ?, ?, ?)",
                  (email_text, intent, reply, purpose, sentiment))
        conn.commit()
        conn.close()

        return jsonify({"intent": intent, "purpose": purpose, "reply": reply, "sentiment": sentiment})

    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    query = request.args.get("q", "")
    filter_intent = request.args.get("intent", "")

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    sql = "SELECT id, email, intent, reply, purpose, important, sentiment FROM emails WHERE 1=1"
    params = []

    if query:
        sql += " AND (email LIKE ? OR purpose LIKE ? OR reply LIKE ?)"
        params += [f"%{query}%", f"%{query}%", f"%{query}%"]

    if filter_intent:
        sql += " AND intent = ?"
        params.append(filter_intent)

    sql += " ORDER BY id DESC"
    c.execute(sql, params)
    rows = c.fetchall()

    # Stats
    c.execute("SELECT intent, COUNT(*) FROM emails GROUP BY intent")
    stats = dict(c.fetchall())

    conn.close()
    return render_template("dashboard.html", emails=rows, stats=stats,
                           query=query, filter_intent=filter_intent)


@app.route("/toggle/<int:email_id>")
def toggle(email_id):
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("UPDATE emails SET important = CASE important WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (email_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:email_id>")
def delete(email_id):
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("DELETE FROM emails WHERE id=?", (email_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


@app.route("/upload", methods=["POST"])
def upload_csv():
    file = request.files["file"]
    if not file.filename.endswith(".csv"):
        return "Only CSV files allowed", 400

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    for line in file.stream.read().decode("utf-8").splitlines():
        if not line.strip():  # skip empty
            continue
        email_text = line.strip()
        intent = classify_email(email_text)
        purpose = summarize_email(email_text)
        reply = generate_reply(email_text, intent)
        sentiment = analyze_sentiment(email_text)

        c.execute("INSERT INTO emails (email, intent, reply, purpose, sentiment) VALUES (?, ?, ?, ?, ?)",
                  (email_text, intent, reply, purpose, sentiment))

    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


@app.route("/export")
def export_csv():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.execute("SELECT email, purpose, intent, reply, sentiment, important FROM emails ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()

    def generate():
        header = ["Email", "Purpose", "Intent", "Reply", "Sentiment", "Important"]
        yield ",".join(header) + "\n"
        for row in rows:
            yield ",".join(['"'+str(item).replace('"','""')+'"' for item in row]) + "\n"

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=emails_export.csv"})


@app.route("/sync_gmail")
def sync_gmail():
    emails = fetch_emails(limit=5)  # last 5 emails
    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    for e in emails:
        intent = classify_email(e["body"])
        purpose = summarize_email(e["body"])
        reply = generate_reply(e["body"], intent)
        sentiment = analyze_sentiment(e["body"])

        c.execute("INSERT INTO emails (email, intent, reply, purpose, sentiment) VALUES (?, ?, ?, ?, ?)",
                  (f"From: {e['from']} | Subject: {e['subject']} | {e['body']}",
                   intent, reply, purpose, sentiment))

    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    print("🚀 Starting Flask Smart Email Reply Agent with Gmail IMAP + Sentiment Analysis...")
    app.run(debug=True)
