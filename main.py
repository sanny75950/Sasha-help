import os
import json
from flask import Flask, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import anthropic
from datetime import datetime

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

conversation_history = {}
TASKS_FILE = "tasks.json"

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def get_system_prompt():
    now = datetime.now().strftime("%A, %d/%m/%Y %H:%M")
    return f"""אתה סוכן אישי חכם ומועיל בשם סשה-בוט, עוזר אישי של סשה.
התאריך והשעה הנוכחיים: {now}
תמיד ענה בעברית אלא אם ביקשו אחרת. היה ידידותי וקצר.
לניהול משימות: [ADD_TASK: תיאור], [DONE_TASK: מספר], [DELETE_TASK: מספר], [SHOW_TASKS]"""

def process_task_commands(response_text, user_phone):
    import re
    tasks = load_tasks()
    if user_phone not in tasks:
        tasks[user_phone] = []
    user_tasks = tasks[user_phone]
    modified = False

    if "[SHOW_TASKS]" in response_text:
        if not user_tasks:
            task_list = "אין לך משימות פתוחות כרגע."
        else:
            task_list = "המשימות שלך:\n"
            for i, task in enumerate(user_tasks, 1):
                status = "v" if task.get("done") else "o"
                task_list += f"{status} {i}. {task['text']}\n"
        response_text = response_text.replace("[SHOW_TASKS]", task_list)

    for match in re.findall(r'\[ADD_TASK: (.+?)\]', response_text):
        user_tasks.append({"text": match, "done": False})
        response_text = response_text.replace(f"[ADD_TASK: {match}]", f"הוספתי: {match}")
        modified = True

    for match in re.findall(r'\[DONE_TASK: (\d+)\]', response_text):
        idx = int(match) - 1
        if 0 <= idx < len(user_tasks):
            user_tasks[idx]["done"] = True
            response_text = response_text.replace(f"[DONE_TASK: {match}]", "סומן כבוצע!")
            modified = True

    for match in sorted(re.findall(r'\[DELETE_TASK: (\d+)\]', response_text), reverse=True):
        idx = int(match) - 1
        if 0 <= idx < len(user_tasks):
            deleted = user_tasks.pop(idx)
            response_text = response_text.replace(f"[DELETE_TASK: {match}]", f"מחקתי: {deleted['text']}")
            modified = True

    if modified:
        tasks[user_phone] = user_tasks
        save_tasks(tasks)
    return response_text

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "")
    if not incoming_msg:
        return str(MessagingResponse())
    if from_number not in conversation_history:
        conversation_history[from_number] = []
    conversation_history[from_number].append({"role": "user", "content": incoming_msg})
    if len(conversation_history[from_number]) > 20:
        conversation_history[from_number] = conversation_history[from_number][-20:]
    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=get_system_prompt(),
            messages=conversation_history[from_number]
        )
        bot_reply = response.content[0].text
        bot_reply = process_task_commands(bot_reply, from_number)
        conversation_history[from_number].append({"role": "assistant", "content": bot_reply})
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
