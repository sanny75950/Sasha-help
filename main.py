
import os
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
from datetime import datetime

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# זיכרון בזמן ריצה
conversation_history = {}
user_profile = {}

def get_system_prompt(phone):
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    profile = user_profile.get(phone, {})
    profile_text = ""
    if profile:
        profile_text = f"\nמה שאתה יודע על המשתמש: {profile}"

    return f"""אתה סשה-בוט — עוזר אישי חכם, מוכוון כלכלה ופיננסים.
התאריך והשעה: {now}
{profile_text}

האישיות שלך:
- תמיד פועל לטובת המשתמש בלבד
- מומחה במניות, שוק ההון וקריפטו
- נותן מידע עדכני, מנתח הזדמנויות
- ישיר, קצר, ומעשי
- זוכר את כל מה שדיברתם
- תמיד מציין סיכונים לצד הזדמנויות

יכולות מיוחדות:
- חיפוש מידע עדכני ברשת (השתמש ב-[SEARCH: שאילתה])
- שמירת מידע על המשתמש (השתמש ב-[REMEMBER: עובדה])
- ניהול משימות ([ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר])

כללים:
- תמיד ענה בעברית
- כשנשאל על מניה/קריפטו — תמיד חפש מידע עדכני
- אל תמליץ ישירות על קנייה/מכירה — תנתח ותציג עובדות
- זכור פרטים אישיים שהמשתמש מספר ושמור אותם"""

def process_commands(response_text, phone, incoming_msg):
    """מעבד פקודות מיוחדות בתשובת הבוט"""
    
    # שמירת מידע על המשתמש
    remember_matches = re.findall(r'\[REMEMBER: (.+?)\]', response_text)
    for match in remember_matches:
        if phone not in user_profile:
            user_profile[phone] = {}
        # שמור כטקסט חופשי
        existing = user_profile[phone].get("notes", "")
        user_profile[phone]["notes"] = existing + " | " + match if existing else match
        response_text = response_text.replace(f"[REMEMBER: {match}]", "")

    # ניהול משימות
    if "tasks" not in user_profile:
        user_profile["tasks_" + phone] = []
    
    tasks = user_profile.get("tasks_" + phone, [])

    if "[SHOW_TASKS]" in response_text:
        if not tasks:
            task_list = "אין לך משימות פתוחות."
        else:
            task_list = "המשימות שלך:\n"
            for i, t in enumerate(tasks, 1):
                status = "v" if t.get("done") else "o"
                task_list += f"{status} {i}. {t['text']}\n"
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
        response_text = response_text.replace(f"[DONE_TASK: {match}]", "סומן כבוצע!")

    return response_text

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "")

    if not incoming_msg:
        return str(MessagingResponse())

    # שמור היסטוריה
    if from_number not in conversation_history:
        conversation_history[from_number] = []

    conversation_history[from_number].append({
        "role": "user",
        "content": incoming_msg
    })

    # שמור רק 30 הודעות אחרונות
    if len(conversation_history[from_number]) > 30:
        conversation_history[from_number] = conversation_history[from_number][-30:]

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(from_number),
            messages=conversation_history[from_number],
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search"
            }]
        )

        # אסוף את כל התשובה כולל תוצאות חיפוש
        bot_reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                bot_reply += block.text

        if not bot_reply:
            bot_reply = "מצטער, לא הצלחתי לעבד את הבקשה."

        bot_reply = process_commands(bot_reply, from_number, incoming_msg)

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
