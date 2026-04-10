import os
import re
import asyncio
import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import base64
import httpx

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# --- מסד נתונים ---
def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                done BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id BIGINT PRIMARY KEY,
                notes TEXT DEFAULT ''
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("✅ מסד נתונים מוכן")
    except Exception as e:
        print(f"DB init error: {e}")

def get_history(user_id, limit=20):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT role, content FROM messages 
            WHERE user_id = %s 
            ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return list(reversed(rows))
    except:
        return []

def save_message(user_id, role, content):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Save message error: {e}")

def get_profile(user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT notes FROM user_profile WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row['notes'] if row else ''
    except:
        return ''

def save_profile(user_id, notes):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO user_profile (user_id, notes) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET notes = %s
        """, (user_id, notes, notes))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Save profile error: {e}")

def get_tasks(user_id):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except:
        return []

def add_task(user_id, text):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO tasks (user_id, text) VALUES (%s, %s)", (user_id, text))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Add task error: {e}")

def done_task(user_id, idx):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall()
        if 0 <= idx < len(rows):
            cur.execute("UPDATE tasks SET done = TRUE WHERE id = %s", (rows[idx]['id'],))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Done task error: {e}")

def delete_task(user_id, idx):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM tasks WHERE user_id = %s ORDER BY created_at", (user_id,))
        rows = cur.fetchall()
        if 0 <= idx < len(rows):
            cur.execute("DELETE FROM tasks WHERE id = %s", (rows[idx]['id'],))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Delete task error: {e}")

# --- System Prompt ---
def get_system_prompt(user_id):
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    notes = get_profile(user_id)
    profile_text = f"\nמה שאתה יודע על המשתמש: {notes}" if notes else ""
    return f"""אתה סשה-בוט — העוזר האישי החכם של סשה.
התאריך והשעה: {now}
{profile_text}

אתה עוזר אישי מקיף — עונה על כל שאלה בכל נושא.
יש לך ידע מעמיק במניות, שוק ההון וקריפטו.
יש לך גישה מלאה לאינטרנט — תמיד חפש מידע עדכני.
אתה יכול לנתח תמונות, מסמכים, ותיקי השקעות.
תמיד פועל לטובת סשה בלבד.
ידידותי, ישיר וקצר. תמיד ענה בעברית.
כשנשאל על השקעות — נתח והצג עובדות, תמיד ציין סיכונים.
זכור פרטים על סשה: [REMEMBER: עובדה]
משימות: [ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר], [DELETE_TASK: מספר]"""

# --- עיבוד פקודות ---
def process_commands(response_text, user_id):
    for match in re.findall(r'\[REMEMBER: (.+?)\]', response_text):
        existing = get_profile(user_id)
        new_notes = existing + " | " + match if existing else match
        save_profile(user_id, new_notes)
        response_text = response_text.replace(f"[REMEMBER: {match}]", "")

    if "[SHOW_TASKS]" in response_text:
        tasks = get_tasks(user_id)
        if not tasks:
            task_list = "אין לך משימות פתוחות."
        else:
            task_list = "המשימות שלך:\n" + "\n".join(
                f"{'✅' if t['done'] else '⬜'} {i}. {t['text']}"
                for i, t in enumerate(tasks, 1))
        response_text = response_text.replace("[SHOW_TASKS]", task_list)

    for match in re.findall(r'\[ADD_TASK: (.+?)\]', response_text):
        add_task(user_id, match)
        response_text = response_text.replace(f"[ADD_TASK: {match}]", f"✅ הוספתי: {match}")

    for match in re.findall(r'\[DONE_TASK: (\d+)\]', response_text):
        done_task(user_id, int(match) - 1)
        response_text = response_text.replace(f"[DONE_TASK: {match}]", "✅ סומן כבוצע!")

    for match in sorted(re.findall(r'\[DELETE_TASK: (\d+)\]', response_text), reverse=True):
        delete_task(user_id, int(match) - 1)
        response_text = response_text.replace(f"[DELETE_TASK: {match}]", "🗑️ נמחק!")

    return response_text.strip()

# --- טיפול בהודעות טקסט ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    incoming_msg = update.message.text

    save_message(user_id, "user", incoming_msg)
    history = get_history(user_id)

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(user_id),
            messages=[{"role": m["role"], "content": m["content"]} for m in history],
            tools=[{"type": "web_search_20250305", "name": "web_search"}]
        )
        bot_reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                bot_reply += block.text
        if not bot_reply:
            bot_reply = "מצטער, לא הצלחתי לעבד."
        bot_reply = process_commands(bot_reply, user_id)[:4000]
        save_message(user_id, "assistant", bot_reply)
    except Exception as e:
        bot_reply = "מצטער, קרתה שגיאה. נסה שוב."
        print(f"Error: {str(e)}")

    await update.message.reply_text(bot_reply)

# --- טיפול בתמונות ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    caption = update.message.caption or "תאר ונתח את התמונה הזו בפירוט"

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(file.file_path)
            image_data = base64.standard_b64encode(response.content).decode("utf-8")

        ai_response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(user_id),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                    {"type": "text", "text": caption}
                ]
            }]
        )
        bot_reply = ai_response.content[0].text
        bot_reply = process_commands(bot_reply, user_id)[:4000]
        save_message(user_id, "user", f"[תמונה] {caption}")
        save_message(user_id, "assistant", bot_reply)
    except Exception as e:
        bot_reply = "מצטער, לא הצלחתי לנתח את התמונה."
        print(f"Photo error: {str(e)}")

    await update.message.reply_text(bot_reply)

# --- הפעלה ---
async def main():
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    print("סשה-בוט Telegram פעיל עם כל הפיצ'רים!")
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
