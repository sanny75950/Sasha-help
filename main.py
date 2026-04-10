import os
import re
import asyncio
import anthropic
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

conversation_history = {}
user_profile = {}

def get_system_prompt(user_id):
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    profile = user_profile.get(user_id, {})
    profile_text = f"\nמה שאתה יודע על המשתמש: {profile['notes']}" if profile.get("notes") else ""
    return f"""אתה סשה-בוט — העוזר האישי החכם של סשה.
התאריך והשעה: {now}
{profile_text}
אתה עוזר אישי מקיף — עונה על כל שאלה בכל נושא.
יש לך ידע מעמיק במניות, שוק ההון וקריפטו.
תמיד פועל לטובת סשה בלבד.
ידידותי, ישיר וקצר. תמיד ענה בעברית.
כשנשאל על השקעות — נתח והצג עובדות, תמיד ציין סיכונים.
זכור פרטים: [REMEMBER: עובדה]
משימות: [ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר]"""

def needs_search(msg):
    keywords = ["מחיר", "שער", "עכשיו", "היום", "חדשות", "ביטקוין",
                "מניה", "דולר", "מזג אוויר", "חופשה", "קריפטו"]
    return any(kw in msg for kw in keywords)

def process_commands(response_text, user_id):
    for match in re.findall(r'\[REMEMBER: (.+?)\]', response_text):
        if user_id not in user_profile:
            user_profile[user_id] = {}
        existing = user_profile[user_id].get("notes", "")
        user_profile[user_id]["notes"] = existing + " | " + match if existing else match
        response_text = response_text.replace(f"[REMEMBER: {match}]", "")
    tasks = user_profile.get("tasks_" + str(user_id), [])
    if "[SHOW_TASKS]" in response_text:
        task_list = "אין משימות." if not tasks else "המשימות שלך:\n" + "\n".join(
            f"{'v' if t.get('done') else 'o'} {i}. {t['text']}"
            for i, t in enumerate(tasks, 1))
        response_text = response_text.replace("[SHOW_TASKS]", task_list)
    for match in re.findall(r'\[ADD_TASK: (.+?)\]', response_text):
        tasks.append({"text": match, "done": False})
        user_profile["tasks_" + str(user_id)] = tasks
        response_text = response_text.replace(f"[ADD_TASK: {match}]", f"הוספתי: {match}")
    for match in re.findall(r'\[DONE_TASK: (\d+)\]', response_text):
        idx = int(match) - 1
        if 0 <= idx < len(tasks):
            tasks[idx]["done"] = True
            user_profile["tasks_" + str(user_id)] = tasks
        response_text = response_text.replace(f"[DONE_TASK: {match}]", "סומן!")
    return response_text.strip()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    incoming_msg = update.message.text
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "user", "content": incoming_msg})
    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]
    try:
        tools = [{"type": "web_search_20250305", "name": "web_search"}] if needs_search(incoming_msg) else []
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(user_id),
            messages=conversation_history[user_id],
            tools=tools if tools else anthropic.NOT_GIVEN
        )
        bot_reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                bot_reply += block.text
        if not bot_reply:
            bot_reply = "מצטער, לא הצלחתי לעבד."
        bot_reply = process_commands(bot_reply, user_id)[:4000]
        conversation_history[user_id].append({"role": "assistant", "content": bot_reply})
    except Exception as e:
        bot_reply = "מצטער, קרתה שגיאה. נסה שוב."
        print(f"Error: {str(e)}")
    await update.message.reply_text(bot_reply)

async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("סשה-בוט Telegram פעיל!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
