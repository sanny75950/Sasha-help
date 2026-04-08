
import os
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
from datetime import datetime

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

האישיות שלך:
- עוזר אישי מקיף — עונה על כל שאלה בכל נושא
- בעל ידע מעמיק במניות, שוק ההון וקריפטו
- תמיד פועל לטובת סשה בלבד
- ידידותי, ישיר וקצר
- זוכר את כל מה שדיברתם בשיחה הנוכחית
- כשרלוונטי — מחפש מידע עדכני ברשת

יכולות:
- עונה על כל שאלה — כללית, עסקית, אישית, טכנית
- מחפש מידע עדכני כשצריך (חדשות, מניות, מזג אוויר וכו')
- מנהל משימות ורשימות ([ADD_TASK: משימה], [SHOW_TASKS], [DONE_TASK: מספר])
- זוכר מידע אישי ([REMEMBER: עובדה])
- בנושאי השקעות — מנתח ומציג עובדות, תמיד מציין סיכונים

כללים:
- תמיד ענה בעברית
- אל תמליץ ישירות על קנייה/מכירה — תנתח ותציג עובדות
- זכור פרטים שסשה מספר ושמור אותם"""

def process_commands(response_text, phone):
    remember_matches = re.findall(r'\[REMEMBER: (.+?)\]', response_text)
    for match in remember_matches:
        if phone not in user_profile:
            user_profile[phone] = {}
        existing = user_profile[phone].get("notes", "")
        user_profile[phone]["notes"] = existing + " | " + match if existing else match
        response_text = response_text.replace(f"[REMEMBER: {match}]", "")

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

    if from_number not in conversation_history:
        conversation_history[from_number] = []

    conversation_history[from_number].append({
        "role": "user",
        "content": incoming_msg
    })

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

        bot_reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                bot_reply += block.text

        if not bot_reply:
            bot_reply = "מצטער, לא הצלחתי לעבד את הבקשה."

        bot_reply = process_commands(bot_reply, from_number)

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
