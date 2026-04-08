import os
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
from datetime import datetime
import threading

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

conversation_history = {}
user_profile = {}

def get_system_prompt(phone):
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    profile = user_profile.get(phone, {})
    profile_text = ""
    if profile.get("notes"):
        profile_text = f"\nמה שאתה יודע על המשתמש: {profile['notes']}"

    return f"""אתה סשה-בוט — העוזר האישי החכם של סשה.
התאריך והשעה: {now}
{profile_text}

אתה עוזר אישי מקיף — עונה על כל שאלה בכל נושא.
יש לך ידע מעמיק במניות, שוק ההון וקריפטו.
תמיד פועל לטובת סשה בלבד.
ידידותי, ישיר וקצר.
תמיד ענה בעברית.
כשנשאל על השקעות — נתח והצג עובדות, תמיד ציין סיכונים.
זכור פרטים שסשה מספר: [REMEMBER: עובדה]
ניהול משימות: [ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר]"""

def process_commands(response_text, phone):
    for match in re.findall(r'\[REMEMBER: (.+?)\]', response_text):
        if phone not in user_profile:
            user_profile[phone] = {}
        existing = user_profile[phone].get("notes", "")
        user_profile[phone]["notes"] = existing + " | " + match if existing else match
        response_text = response_text.replace(f"[REMEMBER: {match}]", "")

    tasks = user_profile.get("tasks_" + phone, [])

    if "[SHOW_TASKS]" in response_text:
        task_list = "אין משימות." if not tasks else "המשימות שלך:\n" + "\n".join(
            f"{'v' if t.get('done') else 'o'} {i}. {t['text']}" 
            for i, t in enumerate(tasks, 1)
        )
        response_text = response_text.replace("[SHOW_TASKS]", task_list)

    for match in re.findall(r'\[ADD_TASK: (.+?)\]', response_text):
        tasks.append({"text": match, "done": False})
        user_profile["tasks_" + phone] = tasks
        response_text = response_text.replace(f"[ADD_TASK: {match}]", f"הוספתי: {match}")

    for match in re.findall(r'\[DONE_TASK: (\d+)\]', response_text):
        idx = int(match) - 1
        if 0 <= idx < len(tasks):
            tasks[idx]["done"] = True
            user_profile["tasks_" + phone] = tasks
        response_text = response_text.replace(f"[DONE_TASK: {match}]", "סומן!")

    return response_text

def needs_search(msg):
    """האם השאלה דורשת חיפוש ברשת?"""
    search_keywords = [
        "מחיר", "שער", "עכשיו", "היום", "חדשות", "עדכון",
        "כמה שווה", "מה קורה", "ביטקוין", "מניה", "נאסד",
        "דולר", "אירו", "מזג אוויר", "חופשה", "טיסה", "מלון"
    ]
    msg_lower = msg.lower()
    return any(kw in msg_lower for kw in search_keywords)

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "")

    if not incoming_msg:
        return str(MessagingResponse())

    if from_number not in conversation_history:
        conversation_history[from_number] = []

    conversation_history[from_number].append({
        "role": "user",
        "content": incoming_msg
    })

    if len(conversation_history[from_number]) > 20:
        conversation_history[from_number] = conversation_history[from_number][-20:]

    try:
        # חיפוש ברשת רק כשצריך
        tools = []
        if needs_search(incoming_msg):
            tools = [{"type": "web_search_20250305", "name": "web_search"}]

        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=get_system_prompt(from_number),
            messages=conversation_history[from_number],
            tools=tools if tools else anthropic.NOT_GIVEN
        )

        bot_reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                bot_reply += block.text

        if not bot_reply:
            bot_reply = "מצטער, לא הצלחתי לעבד."

        bot_reply = process_commands(bot_reply, from_number)
        bot_reply = bot_reply[:1500]  # הגבל אורך

        conversation_history[from_number].append({
            "role": "assistant",
            "content": bot_reply
        })

    except Exception as e:
        bot_reply = "מצטער, קרתה שגיאה. נסה שוב."
        print(f"Error: {str(e)}")

    resp = MessagingResponse()
    resp.message(bot_reply)
    return str(resp)

@app.route("/", methods=["GET"])
def home():
    return "סשה-בוט פעיל!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
