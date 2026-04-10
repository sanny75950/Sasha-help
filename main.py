import os
import re
import asyncio
import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import base64
import httpx
from flask import Flask, request, Response
import json

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
RENDER_URL = os.environ.get("RENDER_URL", "")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
flask_app = Flask(__name__)
ptb_app = None

# --- DB ---
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())")
        cur.execute("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, text TEXT NOT NULL, done BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT NOW())")
        cur.execute("CREATE TABLE IF NOT EXISTS user_profile (user_id BIGINT PRIMARY KEY, notes TEXT DEFAULT '')")
        conn.commit()
        cur.close()
        conn.close()
        print("DB ready")
    except Exception as e:
        print(f"DB error: {e}")

def get_history(user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT role, content FROM messages WHERE user_id = %s ORDER BY created_at DESC LIMIT 20", (user_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return list(reversed(rows))
    except: return []

def save_message(user_id, role, content):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)", (user_id, role, content))
        conn.commit(); cur.close(); conn.close()
    except: pass

def get_profile(user_id):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT notes FROM user_profile WHERE user_id = %s", (user_id,))
        row = cur.fetchone(); cur.close(); conn.close()
        return row['notes'] if row else ''
    except: return ''

def save_profile(user_id, notes):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO user_profile (user_id, notes) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET notes = %s", (user_id, notes, notes))
        conn.commit(); cur.close(); conn.close()
    except: pass

def get_tasks(user_id):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall(); cur.close(); conn.close()
        return rows
    except: return []

def add_task(user_id, text):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("INSERT INTO tasks (user_id, text) VALUES (%s, %s)", (user_id, text))
        conn.commit(); cur.close(); conn.close()
    except: pass

def done_task(user_id, idx):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall()
        if 0 <= idx < len(rows):
            cur.execute("UPDATE tasks SET done = TRUE WHERE id = %s", (rows[idx]['id'],))
            conn.commit()
        cur.close(); conn.close()
    except: pass

def delete_task(user_id, idx):
    try:
        conn = get_db(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall()
        if 0 <= idx < len(rows):
            cur.execute("DELETE FROM tasks WHERE id = %s", (rows[idx]['id'],))
            conn.commit()
        cur.close(); conn.close()
    except: pass

def get_system_prompt(user_id):
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    notes = get_profile(user_id)
    profile_text = f"\nמה שאתה יודע על המשתמש: {notes}" if notes else ""
    return f"""אתה סשה-בוט — העוזר האישי החכם של סשה.
התאריך והשעה: {now}{profile_text}
אתה עוזר אישי מקיף — עונה על כל שאלה בכל נושא.
יש לך ידע מעמיק במניות, שוק ההון וקריפטו.
יש לך גישה מלאה לאינטרנט — תמיד חפש מידע עדכני.
אתה יכול לנתח תמונות ומסמכים.
תמיד פועל לטובת סשה בלבד. ידידותי, ישיר וקצר. תמיד ענה בעברית.
זכור פרטים: [REMEMBER: עובדה]
משימות: [ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר], [DELETE_TASK: מספר]"""

def process_commands(text, user_id):
    for match in re.findall(r'\[REMEMBER: (.+?)\]', text):
        existing = get_profile(user_id)
        save_profile(user_id, existing + " | " + match if existing else match)
        text = text.replace(f"[REMEMBER: {match}]", "")
    if "[SHOW_TASKS]" in text:
        tasks = get_tasks(user_id)
        task_list = "אין משימות." if not tasks else "המשימות שלך:\n" + "\n".join(
            f"{'✅' if t['done'] else '⬜'} {i}. {t['text']}" for i, t in enumerate(tasks, 1))
        text = text.replace("[SHOW_TASKS]", task_list)
    for match in re.findall(r'\[ADD_TASK: (.+?)\]', text):
        add_task(user_id, match)
        text = text.replace(f"[ADD_TASK: {match}]", f"✅ הוספתי: {match}")
    for match in re.findall(r'\[DONE_TASK: (\d+)\]', text):
        done_task(user_id, int(match) - 1)
        text = text.replace(f"[DONE_TASK: {match}]", "✅ סומן!")
    for match in sorted(re.findall(r'\[DELETE_TASK: (\d+)\]', text), reverse=True):
        delete_task(user_id, int(match) - 1)
        text = text.replace(f"[DELETE_TASK: {match}]", "🗑️ נמחק!")
    return text.strip()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message.text
    save_message(user_id, "user", msg)
    history = get_history(user_id)
    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(user_id),
            messages=[{"role": m["role"], "content": m["content"]} for m in history],
            tools=[{"type": "web_search_20250305", "name": "web_search"}]
        )
        reply = "".join(b.text for b in response.content if hasattr(b, "text"))
        if not reply: reply = "מצטער, לא הצלחתי לעבד."
        reply = process_commands(reply, user_id)[:4000]
        save_message(user_id, "assistant", reply)
    except Exception as e:
        reply = "מצטער, קרתה שגיאה. נסה שוב."
        print(f"Error: {e}")
    await update.message.reply_text(reply)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    caption = update.message.caption or "תאר ונתח את התמונה"
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            img = base64.standard_b64encode(resp.content).decode("utf-8")
        r = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1024,
            system=get_system_prompt(user_id),
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
                {"type": "text", "text": caption}
            ]}]
        )
        reply = r.content[0].text
        reply = process_commands(reply, user_id)[:4000]
        save_message(user_id, "user", f"[תמונה] {caption}")
        save_message(user_id, "assistant", reply)
    except Exception as e:
        reply = "מצטער, לא הצלחתי לנתח את התמונה."
        print(f"Photo error: {e}")
    await update.message.reply_text(reply)

@flask_app.route("/")
def home():
    return "סשה-בוט פעיל! 🤖", 200

@flask_app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    if ptb_app is None:
        return Response("not ready", status=503)
    data = request.get_json(force=True)
    asyncio.run_coroutine_threadsafe(
        ptb_app.process_update(Update.de_json(data, ptb_app.bot)),
        asyncio.get_event_loop()
    )
    return Response("ok", status=200)

async def setup_webhook():
    global ptb_app
    init_db()
    ptb_app = ApplicationBuilder().token(TELEGRAM_TOKEN).updater(None).build()
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    await ptb_app.initialize()
    await ptb_app.start()
    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_TOKEN}"
    await ptb_app.bot.set_webhook(webhook_url)
    print(f"Webhook set: {webhook_url}")

def run_async_setup():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup_webhook())
    loop.run_forever()

if __name__ == "__main__":
    import threading
    t = threading.Thread(target=run_async_setup, daemon=True)
    t.start()
    import time; time.sleep(3)
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
