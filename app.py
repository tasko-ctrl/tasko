import base64
import datetime
import os
import random
from flask import Flask, jsonify, request
from flask_cors import CORS
from google import genai
from google.api_core import exceptions
from google.genai import types
import requests

app = Flask(__name__)
CORS(app)  # Enables cross-origin requests for your frontend

client = genai.Client() if os.environ.get("GEMINI_API_KEY") else None

USERS_DB = {}
PENDING_USERS_DB = {}
OTP_DB = {}
TASKS_DB = []
DAILY_ROUTINES_DB = []
DAILY_LOGS_DB = {}
HISTORY_DB = []
MESSAGES_DB = []


def send_otp_email(receiver_email, otp_code):
  brevo_api_key = os.environ.get("BREVO_API_KEY")
  sender_email = os.environ.get("SENDER_EMAIL") or os.environ.get(
      "SMTP_EMAIL"
  )

  if not brevo_api_key or not sender_email:
    print(f"\n[TASKO OTP FALLBACK] Code for {receiver_email}: {otp_code}\n")
    return

  url = "https://api.brevo.com/v3/smtp/email"
  headers = {
      "accept": "application/json",
      "api-key": brevo_api_key,
      "content-type": "application/json",
  }
  payload = {
      "sender": {"email": sender_email, "name": "Tasko Workspace"},
      "to": [{"email": receiver_email}],
      "subject": "Tasko - Verification Code",
      "htmlContent": (
          f"<html><body><h3>Welcome to Tasko Workspace!</h3><p>Your"
          f" verification code is: <b>{otp_code}</b></p></body></html>"
      ),
  }

  try:
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    if response.status_code in [200, 201, 202]:
      print(f"Brevo OTP successfully sent to {receiver_email}")
    else:
      print(
          f"Failed to send email via Brevo: {response.status_code} -"
          f" {response.text}"
      )
      print(f"\n[TASKO OTP FALLBACK] Code for {receiver_email}: {otp_code}\n")
  except Exception as e:
    print(f"Error sending email: {e}")
    print(f"\n[TASKO OTP FALLBACK] Code for {receiver_email}: {otp_code}\n")


@app.route("/api/signup-request", methods=["POST"])
def signup_request():
  data = request.json
  email = data.get("email")
  if email in USERS_DB or email in PENDING_USERS_DB:
    return jsonify({"error": "Email already registered."}), 400

  otp = str(random.randint(100000, 999999))
  OTP_DB[email] = otp
  send_otp_email(email, otp)
  return jsonify({"success": True, "message": "OTP generated and sent."})


@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
  data = request.json
  email = data.get("email")
  user_otp = data.get("otp")
  signup_data = data.get("signup_data", {})

  if email not in OTP_DB or OTP_DB[email] != user_otp:
    return jsonify({"error": "Invalid or expired OTP code."}), 400

  OTP_DB.pop(email, None)

  name = signup_data.get("name")
  password = signup_data.get("password")
  role = signup_data.get("role", "Employee")

  user_data = {
      "name": name,
      "email": email,
      "password": password,
      "role": role,
      "status": "Jobless",
  }

  if role == "CEO":
    company_id = str(random.randint(1000000000, 9999999999))
    user_data["company_id"] = company_id
    user_data["company_name"] = signup_data.get("company_name", "MyCorp")
    user_data["status"] = "Approved"
    USERS_DB[email] = user_data
    HISTORY_DB.insert(
        0,
        {
            "company_id": company_id,
            "action": (
                f"Company '{user_data['company_name']}' created by CEO {name}"
            ),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
  else:
    company_id = signup_data.get("company_id")
    user_data["company_id"] = company_id
    PENDING_USERS_DB[email] = user_data

  return jsonify({"success": True, "user": user_data})


@app.route("/api/login", methods=["POST"])
def login():
  data = request.json
  user = USERS_DB.get(data.get("email")) or PENDING_USERS_DB.get(
      data.get("email")
  )
  if not user or user.get("password") != data.get("password"):
    return jsonify({"error": "Invalid email or password."}), 401
  return jsonify({"success": True, "user": user})


@app.route("/api/data", methods=["GET"])
def get_data():
  company_id = request.args.get("company_id")
  today_date = datetime.datetime.now().strftime("%Y-%m-%d")

  today_logs = DAILY_LOGS_DB.get(today_date, {})
  routines_with_status = []
  for idx, r in enumerate(DAILY_ROUTINES_DB):
    if str(r.get("company_id")) == str(company_id):
      r_copy = r.copy()
      r_copy["today_status"] = today_logs.get(idx, "Pending")
      routines_with_status.append(r_copy)

  return jsonify({
      "tasks": [
          t for t in TASKS_DB if str(t.get("company_id")) == str(company_id)
      ],
      "routines": routines_with_status,
      "history": [
          h
          for h in HISTORY_DB
          if str(h.get("company_id")) == str(company_id)
          or "company_id" not in h
      ],
  })


@app.route("/api/tasks", methods=["POST"])
def add_task():
  data = request.json
  title = data.get("title")
  assigned_to = data.get("assigned_to")
  deadline = data.get("deadline")
  priority = data.get("priority", "Medium")
  company_id = data.get("company_id")

  TASKS_DB.append({
      "title": title,
      "assigned_to": assigned_to,
      "deadline": deadline,
      "priority": priority,
      "status": "Pending",
      "ai_comment": "",
      "company_id": company_id,
  })

  HISTORY_DB.insert(
      0,
      {
          "company_id": company_id,
          "action": (
              f"Task '{title}' assigned to {assigned_to} (Deadline:"
              f" {deadline or 'None'})"
          ),
          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
      },
  )
  return jsonify({"success": True})


@app.route("/api/routines", methods=["POST"])
def add_routine():
  data = request.json
  title = data.get("title")
  assigned_to = data.get("assigned_to")
  company_id = data.get("company_id")

  DAILY_ROUTINES_DB.append(
      {"title": title, "assigned_to": assigned_to, "company_id": company_id}
  )

  HISTORY_DB.insert(
      0,
      {
          "company_id": company_id,
          "action": f"Auto-daily routine '{title}' set for {assigned_to}",
          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
      },
  )
  return jsonify({"success": True})


@app.route("/api/routines/complete", methods=["POST"])
def complete_routine():
  data = request.json
  index = data.get("index")
  today_date = datetime.datetime.now().strftime("%Y-%m-%d")

  if not (0 <= index < len(DAILY_ROUTINES_DB)):
    return jsonify({"error": "Routine not found."}), 404

  if today_date not in DAILY_LOGS_DB:
    DAILY_LOGS_DB[today_date] = {}

  DAILY_LOGS_DB[today_date][index] = "Completed"
  routine = DAILY_ROUTINES_DB[index]

  HISTORY_DB.insert(
      0,
      {
          "company_id": routine.get("company_id"),
          "action": (
              f"Daily routine '{routine['title']}' marked completed for today by"
              f" {routine['assigned_to']}"
          ),
          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
      },
  )
  return jsonify({"success": True})


@app.route("/api/tasks/verify-proof", methods=["POST"])
def verify_proof():
  data = request.json
  index = data.get("index")
  image_b64 = data.get("image")
  mime_type = data.get("mime_type", "image/jpeg")

  if not (0 <= index < len(TASKS_DB)):
    return jsonify({"error": "Task not found."}), 404

  task = TASKS_DB[index]
  if not client:
    task["status"] = "Completed"
    task["ai_comment"] = "Simulated verification (API key missing)."
    return jsonify({"success": True, "status": "Completed"})

  try:
    image_bytes = base64.b64decode(image_b64)
    prompt = (
        f"Evaluate if the image proves task completion: '{task['title']}'."
        " Respond strictly in format: STATUS: [Completed OR Failed] | REASON:"
        " [explanation]."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
    )
    ai_text = response.text
    task["status"] = "Completed" if "Completed" in ai_text else "Failed"
    task["ai_comment"] = ai_text

    HISTORY_DB.insert(
        0,
        {
            "company_id": task.get("company_id"),
            "action": (
                f"AI verified task '{task['title']}' for"
                f" {task['assigned_to']}: {task['status']}"
            ),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    return jsonify({"success": True, "status": task["status"]})
  except Exception as e:
    return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["GET", "POST"])
def workspace_chat():
  if request.method == "POST":
    data = request.json
    company_id = data.get("company_id")
    MESSAGES_DB.append({
        "company_id": company_id,
        "sender": data.get("sender"),
        "text": data.get("text"),
        "time": datetime.datetime.now().strftime("%H:%M"),
    })
    return jsonify({"success": True})
  else:
    company_id = request.args.get("company_id")
    filtered_msgs = [
        m for m in MESSAGES_DB if str(m.get("company_id")) == str(company_id)
    ]
    return jsonify({"messages": filtered_msgs})


@app.route("/api/admin/pending-users", methods=["GET"])
def get_pending_users():
  company_id = request.args.get("company_id")
  return jsonify({
      "pending_users": [
          u
          for u in PENDING_USERS_DB.values()
          if str(u.get("company_id")) == str(company_id)
      ]
  })


@app.route("/api/admin/approve-user", methods=["POST"])
def approve_user():
  data = request.json
  email = data.get("email")
  if email in PENDING_USERS_DB:
    user = PENDING_USERS_DB.pop(email)
    user["status"] = "Approved"
    USERS_DB[email] = user
    HISTORY_DB.insert(
        0,
        {
            "company_id": user.get("company_id"),
            "action": f"Employee {user['name']} approved into workspace",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    return jsonify({"success": True})
  return jsonify({"error": "User not found."}), 404


@app.route("/api/admin/employees-list", methods=["GET"])
def get_employees_list():
  company_id = request.args.get("company_id")
  return jsonify({
      "employees": [
          {
              "name": u.get("name"),
              "email": u.get("email"),
              "role": u.get("role"),
          }
          for u in USERS_DB.values()
          if str(u.get("company_id")) == str(company_id)
          and u.get("status") == "Approved"
      ]
  })


@app.route("/api/admin/kick-employee", methods=["POST"])
def kick_employee():
  data = request.json
  email = data.get("email")
  company_id = data.get("company_id")
  if email in USERS_DB:
    user = USERS_DB.pop(email)
    HISTORY_DB.insert(
        0,
        {
            "company_id": company_id,
            "action": f"Employee {user.get('name')} was kicked from the workspace",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    return jsonify({"success": True})
  return jsonify({"error": "Employee not found."}), 404


@app.route("/api/admin/update-company", methods=["POST"])
def update_company():
  data = request.json
  company_id = data.get("company_id")
  new_name = data.get("company_name")
  for u in USERS_DB.values():
    if str(u.get("company_id")) == str(company_id):
      u["company_name"] = new_name
  HISTORY_DB.insert(
      0,
      {
          "company_id": company_id,
          "action": f"Workspace company name updated to '{new_name}'",
          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
      },
  )
  return jsonify({"success": True})


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)