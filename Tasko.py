import datetime
import os
import base64
import random
import io
import html
import json
import sqlite3
from functools import wraps
from flask import Flask, jsonify, request, render_template, render_template_string, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
import jwt
import requests

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # Cap uploads at 8 MB to prevent OOM crashes

# --- Environment & Security Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-railway-variables-super-secret")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
APPLE_CLIENT_ID = os.environ.get("APPLE_CLIENT_ID", "com.tasko.workspace.app")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# --- Structured AI Output Pydantic Schema ---
class TaskProofEvaluation(BaseModel):
    passed: bool = Field(description="True if the image genuinely fulfills the task requirements, False otherwise.")
    reason: str = Field(description="A concise professional explanation for why the proof passed or failed.")


# --- SQLite Persistent Storage Layer (Concurrency-Safe) ---
DB_DIR = "tasko_data"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "tasko.db")

os.makedirs("templates", exist_ok=True)
LANDING_HTML_PATH = os.path.join("templates", "landing.html")
if not os.path.exists(LANDING_HTML_PATH):
    with open(LANDING_HTML_PATH, "w", encoding="utf-8") as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko Workspace - Welcome</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #07090e; color: #ededef; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: #0f1523; padding: 40px; border-radius: 16px; border: 1px solid #1a2234; text-align: center; max-width: 400px; box-shadow: 0 10px 35px rgba(0,0,0,0.5); }
        a.btn { display: inline-block; background: #5e6ad2; color: white; padding: 12px 24px; border-radius: 10px; text-decoration: none; font-weight: 500; margin-top: 20px; transition: background 0.2s; }
        a.btn:hover { background: #727ffd; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Tasko Workspace</h2>
        <p style="color: #8a95a5; font-size: 0.9em;">Enterprise workspace management with AI-powered task verification.</p>
        <a href="/app" class="btn">Launch Workspace App</a>
    </div>
</body>
</html>""")


def get_db():
    # check_same_thread=False is required because Flask handles requests across worker threads
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                password_hash TEXT,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                stance TEXT DEFAULT '',
                permissions TEXT DEFAULT '[]',
                company_id TEXT NOT NULL,
                company_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                assigned_to TEXT NOT NULL,
                deadline TEXT,
                priority TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'Pending',
                ai_comment TEXT DEFAULT '',
                company_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                assigned_to TEXT NOT NULL,
                company_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_logs (
                log_date TEXT NOT NULL,
                routine_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (log_date, routine_id)
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL,
                action TEXT NOT NULL,
                time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                text TEXT NOT NULL,
                time TEXT NOT NULL
            );
        """)


init_db()


def log_history(conn, company_id, action):
    conn.execute(
        "INSERT INTO history (company_id, action, time) VALUES (?, ?, ?)",
        (str(company_id), action, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    )


def serialize_user(row):
    return {
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "status": row["status"],
        "stance": row["stance"],
        "permissions": json.loads(row["permissions"] or "[]"),
        "company_id": row["company_id"],
        "company_name": row["company_name"]
    }


# --- JWT Token & Auth Middleware ---
def create_token(user_dict):
    payload = {
        "email": user_dict["email"],
        "company_id": str(user_dict["company_id"]),
        "role": user_dict["role"],
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid authorization token."}), 401
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expired. Please sign in again."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid session token."}), 401

        with get_db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (payload["email"],)).fetchone()
            if not row:
                return jsonify({"error": "User account no longer exists."}), 401
            request.current_user = serialize_user(row)
        return f(*args, **kwargs)
    return decorated


def has_permission(user, perm):
    return user["role"] == "CEO" or perm in user.get("permissions", [])


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko - Workspace Manager</title>
    <link rel="icon" type="image/png" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/favicon.ico">
    
    <!-- Google & Apple Auth SDKs -->
    <script src="https://accounts.google.com/gsi/client" async defer></script>
    <script type="text/javascript" src="https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js"></script>

    <style>
        :root {
            --bg-color: #07090e;
            --sidebar-bg: #0b0f17;
            --card-bg: #0f1523;
            --card-hover: #141c2e;
            --text-color: #ededef;
            --border-color: #1a2234;
            --input-bg: #090d16;
            --subtext: #8a95a5;
            --accent: #5e6ad2;
            --accent-hover: #727ffd;
            --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
            --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        .light-theme {
            --bg-color: #f8fafc;
            --sidebar-bg: #f1f5f9;
            --card-bg: #ffffff;
            --card-hover: #fcfdff;
            --text-color: #0f172a;
            --border-color: #e2e8f0;
            --input-bg: #f8fafc;
            --subtext: #64748b;
            --accent: #5e6ad2;
            --accent-hover: #4f58bc;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; display: flex; height: 100vh; overflow: hidden; transition: background 0.4s var(--ease-out-expo), color 0.4s var(--ease-out-expo); }
        
        .sidebar { width: 265px; min-width: 265px; background: var(--sidebar-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 24px; box-sizing: border-box; height: 100vh; transition: all 0.3s var(--ease-out-expo); }
        .sidebar-brand { font-size: 1.15em; font-weight: 600; display: flex; align-items: center; gap: 10px; margin-bottom: 32px; color: var(--text-color); letter-spacing: -0.01em; animation: fadeInDown 0.5s var(--ease-out-expo); }
        .sidebar-menu { display: flex; flex-direction: column; gap: 6px; flex: 1; overflow-y: auto; }
        
        .nav-item { padding: 11px 14px; border-radius: 10px; cursor: pointer; color: var(--subtext); font-weight: 500; font-size: 0.9em; transition: all 0.25s var(--ease-out-expo); border: none; background: transparent; text-align: left; width: 100%; display: flex; align-items: center; gap: 10px; box-sizing: border-box; position: relative; overflow: hidden; }
        .nav-item::after { content: ''; position: absolute; left: 0; bottom: 0; width: 3px; height: 0; background: var(--accent); transition: height 0.2s var(--ease-out-expo); border-radius: 0 4px 4px 0; }
        .nav-item:hover { background: rgba(255,255,255,0.04); color: var(--text-color); transform: translateX(4px); }
        .nav-item.active { background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); box-shadow: 0 8px 24px rgba(0,0,0,0.3); transform: translateX(4px); }
        .nav-item.active::after { height: 60%; }

        .main-content { flex: 1; overflow-y: auto; padding: 48px; box-sizing: border-box; position: relative; }
        
        @keyframes fadeInScale {
            from { opacity: 0; transform: translateY(14px) scale(0.98); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes fadeInDown {
            from { opacity: 0; transform: translateY(-10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes spinLoader {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .section-view { animation: fadeInScale 0.4s var(--ease-out-expo) forwards; }

        .card { background: var(--card-bg); padding: 32px; border-radius: 16px; margin-bottom: 24px; box-shadow: 0 10px 35px -8px rgba(0,0,0,0.5); border: 1px solid var(--border-color); transition: all 0.3s var(--ease-out-expo); position: relative; overflow: hidden; z-index: 10; }
        .card:hover { border-color: rgba(94, 106, 210, 0.4); transform: translateY(-3px); box-shadow: 0 16px 45px -10px rgba(94, 106, 210, 0.2); }
        
        input, select, textarea { padding: 12px 16px; margin: 8px 0 18px 0; border-radius: 10px; border: 1px solid var(--border-color); background: var(--input-bg); color: var(--text-color); width: 100%; box-sizing: border-box; font-size: 0.9em; outline: none; transition: all 0.25s var(--ease-out-expo); position: relative; z-index: 15; }
        input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(94, 106, 210, 0.18), 0 4px 12px rgba(0,0,0,0.2); background: var(--card-bg); transform: translateY(-1px); }
        
        button { background: var(--accent); cursor: pointer; font-weight: 500; border: none; color: white; padding: 12px 20px; border-radius: 10px; width: 100%; font-size: 0.9em; transition: all 0.25s var(--ease-spring); display: inline-flex; align-items: center; justify-content: center; gap: 8px; box-shadow: 0 4px 16px rgba(94, 106, 210, 0.35); position: relative; overflow: hidden; z-index: 20; }
        button:hover { background: var(--accent-hover); transform: translateY(-2px); box-shadow: 0 8px 25px rgba(94, 106, 210, 0.5); }
        button:active { transform: translateY(1px) scale(0.97); box-shadow: 0 2px 10px rgba(94, 106, 210, 0.3); }
        
        .social-btn { background: var(--input-bg); color: var(--text-color); border: 1px solid var(--border-color); margin-bottom: 10px; box-shadow: none; font-weight: 500; }
        .social-btn:hover { background: var(--card-hover); border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.3); }

        #globalLoader { position: fixed; inset: 0; background: rgba(7, 9, 14, 0.85); backdrop-filter: blur(6px); z-index: 99999; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; opacity: 0; visibility: hidden; pointer-events: none; transition: opacity 0.3s var(--ease-out-expo), visibility 0.3s var(--ease-out-expo); }
        #globalLoader.active { opacity: 1; visibility: visible; pointer-events: auto; }
        .spinner-ring { width: 44px; height: 44px; border: 3px solid rgba(94, 106, 210, 0.2); border-top-color: var(--accent); border-radius: 50%; animation: spinLoader 0.7s cubic-bezier(0.4, 0, 0.2, 1) infinite; box-shadow: 0 0 20px rgba(94, 106, 210, 0.4); }

        .hidden { display: none !important; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9em; }
        th, td { padding: 14px; border-bottom: 1px solid var(--border-color); text-align: left; transition: background 0.2s; }
        tr:hover td { background: rgba(255,255,255,0.015); }
        th { background: var(--input-bg); color: var(--subtext); font-weight: 600; text-transform: uppercase; font-size: 0.75em; letter-spacing: 0.05em; }
        
        .badge-done { background: rgba(5, 150, 105, 0.15); color: #34d399; border: 1px solid rgba(5, 150, 105, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .badge-pending { background: rgba(217, 119, 6, 0.15); color: #fbbf24; border: 1px solid rgba(217, 119, 6, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .badge-failed { background: rgba(220, 38, 38, 0.15); color: #f87171; border: 1px solid rgba(220, 38, 38, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        
        @keyframes pulseGlow {
            0% { transform: scale(0.92); opacity: 0.8; }
            50% { transform: scale(1.1); opacity: 1; box-shadow: 0 0 14px currentColor; }
            100% { transform: scale(0.92); opacity: 0.8; }
        }
        .pulsing-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; animation: pulseGlow 1.8s infinite ease-in-out; }

        #toastContainer { position: fixed; top: 24px; right: 24px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
        .toast { background: var(--card-bg); border: 1px solid var(--border-color); color: var(--text-color); padding: 14px 22px; border-radius: 12px; box-shadow: 0 12px 35px rgba(0,0,0,0.5); font-size: 0.9em; display: flex; align-items: center; gap: 12px; pointer-events: auto; animation: toastIn 0.35s var(--ease-out-expo) forwards; min-width: 300px; backdrop-filter: blur(8px); }
        .toast.success { border-left: 4px solid #34d399; }
        .toast.error { border-left: 4px solid #f87171; }
        .toast.info { border-left: 4px solid var(--accent); }
        @keyframes toastIn {
            from { opacity: 0; transform: translateY(-20px) scale(0.95); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes toastOut {
            from { opacity: 1; transform: translateY(0) scale(1); }
            to { opacity: 0; transform: translateY(-20px) scale(0.95); }
        }

        .chat-box { height: 400px; overflow-y: scroll; border: 1px solid var(--border-color); background: var(--input-bg); padding: 18px; border-radius: 12px; margin-bottom: 16px; scroll-behavior: smooth; }
        .chat-message { margin-bottom: 14px; font-size: 0.9em; line-height: 1.5; animation: fadeInScale 0.3s var(--ease-out-expo); }
        h2, h3, h4 { margin-top: 0; font-weight: 600; letter-spacing: -0.01em; }
        a { color: var(--accent); text-decoration: none; transition: color 0.15s; }
        a:hover { color: var(--accent-hover); text-decoration: underline; }

        .kanban-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-top: 20px; }
        .kanban-col { background: var(--input-bg); border: 1px solid var(--border-color); border-radius: 14px; padding: 18px; min-height: 450px; display: flex; flex-direction: column; }
        .kanban-col-header { font-weight: 600; font-size: 0.9em; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid var(--border-color); color: var(--subtext); text-transform: uppercase; letter-spacing: 0.05em; }
        .kanban-cards { flex: 1; display: flex; flex-direction: column; gap: 14px; min-height: 100px; }
        .kanban-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 16px; cursor: grab; transition: all 0.2s var(--ease-out-expo); }
        .kanban-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
        .kanban-card:active { cursor: grabbing; }

        #cmdPaletteModal { position: fixed; inset: 0; background: rgba(7, 9, 14, 0.8); backdrop-filter: blur(8px); z-index: 99999; display: flex; align-items: flex-start; justify-content: center; padding-top: 12vh; opacity: 0; visibility: hidden; pointer-events: none; transition: opacity 0.2s var(--ease-out-expo), visibility 0.2s var(--ease-out-expo); }
        #cmdPaletteModal.active { opacity: 1; visibility: visible; pointer-events: auto; }
        .cmd-box { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 16px; width: 100%; max-width: 600px; box-shadow: 0 25px 60px rgba(0,0,0,0.7); overflow: hidden; animation: fadeInScale 0.3s var(--ease-out-expo); }
        .cmd-item { padding: 12px 18px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; transition: background 0.15s; font-size: 0.9em; color: var(--text-color); border-bottom: 1px solid var(--border-color); }
        .cmd-item:hover, .cmd-item.selected { background: rgba(94, 106, 210, 0.15); color: var(--accent); }

        #feedbackModal { position: fixed; inset: 0; background: rgba(7, 9, 14, 0.85); backdrop-filter: blur(8px); z-index: 99999; display: flex; align-items: center; justify-content: center; opacity: 0; visibility: hidden; pointer-events: none; transition: opacity 0.2s var(--ease-out-expo), visibility 0.2s var(--ease-out-expo); }
        #feedbackModal.active { opacity: 1; visibility: visible; pointer-events: auto; }
    </style>
</head>
<body>
    <div id="toastContainer"></div>
    <div id="globalLoader">
        <div class="spinner-ring"></div>
        <div style="font-size:0.9em; font-weight:500; color:var(--text-color); letter-spacing:-0.01em;" id="loaderText">Authenticating with Workspace...</div>
    </div>

    <!-- Feedback Modal -->
    <div id="feedbackModal" onclick="if(event.target === this) toggleFeedbackModal()">
        <div class="card" style="width: 100%; max-width: 500px; margin: 0; max-height: 85vh; display: flex; flex-direction: column;">
            <h3 style="margin-bottom: 6px;">Send Workspace Feedback</h3>
            <p style="font-size: 0.85em; color: var(--subtext); margin-bottom: 16px;">Select your preferred email platform or service to open a pre-filled draft to <b>taskofeedback@gmail.com</b>.</p>
            
            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Choose Email Platform / Client</label>
            <select id="feedbackPlatformSelect" style="margin-bottom: 16px;">
                <optgroup label="Personal Email">
                    <option value="gmail">Gmail</option>
                    <option value="outlook">Microsoft Outlook</option>
                    <option value="icloud">Apple iCloud Mail</option>
                    <option value="yahoo">Yahoo Mail</option>
                    <option value="proton">Proton Mail</option>
                    <option value="tuta">Tuta</option>
                    <option value="aol">AOL Mail</option>
                    <option value="gmx">GMX Mail</option>
                    <option value="mailcom">Mail.com</option>
                    <option value="yandex">Yandex Mail</option>
                    <option value="zoho_personal">Zoho Mail (Personal)</option>
                    <option value="rediffmail">Rediffmail</option>
                </optgroup>
                <optgroup label="Business Email Hosting">
                    <option value="google_workspace">Google Workspace</option>
                    <option value="m365">Microsoft 365</option>
                    <option value="zoho_business">Zoho Mail (Business)</option>
                    <option value="titan">Titan</option>
                    <option value="neo">Neo</option>
                    <option value="fastmail">Fastmail</option>
                    <option value="rackspace">Rackspace Email</option>
                    <option value="proton_biz">Proton for Business</option>
                    <option value="namecheap">Namecheap Private Email</option>
                    <option value="hostinger">Hostinger Business Email</option>
                </optgroup>
                <optgroup label="Email Marketing & Newsletters">
                    <option value="mailchimp">Mailchimp</option>
                    <option value="klaviyo">Klaviyo</option>
                    <option value="brevo">Brevo</option>
                    <option value="activecampaign">ActiveCampaign</option>
                    <option value="constantcontact">Constant Contact</option>
                    <option value="mailerlite">MailerLite</option>
                    <option value="kit">Kit (formerly ConvertKit)</option>
                    <option value="beehiiv">Beehiiv</option>
                    <option value="substack">Substack</option>
                    <option value="omnisend">Omnisend</option>
                    <option value="getresponse">GetResponse</option>
                    <option value="hubspot">HubSpot Email Marketing</option>
                    <option value="moosend">Moosend</option>
                    <option value="emailoctopus">EmailOctopus</option>
                    <option value="campaignmonitor">Campaign Monitor</option>
                    <option value="aweber">AWeber</option>
                    <option value="drip">Drip</option>
                </optgroup>
                <optgroup label="Email Clients & Unified Inbox Apps">
                    <option value="thunderbird">Mozilla Thunderbird</option>
                    <option value="apple_mail">Apple Mail</option>
                    <option value="spark">Spark</option>
                    <option value="shortwave">Shortwave</option>
                    <option value="superhuman">Superhuman</option>
                    <option value="emclient">eM Client</option>
                    <option value="mailbird">Mailbird</option>
                    <option value="mailspring">Mailspring</option>
                    <option value="canary">Canary Mail</option>
                    <option value="bluemail">BlueMail</option>
                    <option value="edison">Edison Mail</option>
                </optgroup>
                <optgroup label="Transactional Email APIs">
                    <option value="ses">Amazon SES</option>
                    <option value="sendgrid">SendGrid</option>
                    <option value="postmark">Postmark</option>
                    <option value="mailgun">Mailgun</option>
                    <option value="resend">Resend</option>
                    <option value="brevo_smtp">Brevo SMTP</option>
                    <option value="mailjet">Mailjet</option>
                    <option value="zeptomail">ZeptoMail</option>
                </optgroup>
            </select>

            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Your Feedback Message</label>
            <textarea id="feedbackText" rows="4" placeholder="Describe your feedback, feature request, or bug report here..." style="margin-bottom: 20px; resize: none;"></textarea>

            <div style="display: flex; gap: 10px;">
                <button type="button" onclick="toggleFeedbackModal()" style="background: var(--input-bg); border: 1px solid var(--border-color); color: var(--text-color); box-shadow: none;">Cancel</button>
                <button type="button" onclick="submitFeedbackDraft()">Open Draft in Selected Platform</button>
            </div>
        </div>
    </div>

    <!-- Command Palette Modal (Ctrl+K) -->
    <div id="cmdPaletteModal" onclick="if(event.target === this) toggleCommandPalette()">
        <div class="cmd-box">
            <div style="padding: 16px 20px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; gap: 12px;">
                <span style="color: var(--accent); font-weight: bold;">⌘</span>
                <input type="text" id="cmdInput" placeholder="Type a command or search actions (e.g. Assigned Tasks, Theme, Create Task)..." oninput="filterCommands()" style="margin: 0; border: none; background: transparent; font-size: 1em;" autofocus>
                <span style="font-size: 0.75em; background: var(--input-bg); border: 1px solid var(--border-color); padding: 4px 8px; border-radius: 6px; color: var(--subtext);">ESC</span>
            </div>
            <div id="cmdResultsList" style="max-height: 350px; overflow-y: auto;"></div>
        </div>
    </div>

    <!-- Workspace ID Modal for Social Logins -->
    <div id="socialWorkspaceModal" class="hidden" style="position:fixed; inset:0; background:rgba(7, 9, 14, 0.85); backdrop-filter:blur(6px); z-index:9999; display:flex; align-items:center; justify-content:center;">
        <div class="card" style="width:100%; max-width:400px; margin:0;">
            <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;" id="socialModalTitle">Enter Workspace ID</h3>
            <p style="font-size:0.85em; color:var(--subtext); margin-bottom:14px;">Please provide your company's Workspace ID to link your social account.</p>
            <input type="text" id="socialWorkspaceIdInput" placeholder="Workspace Company ID">
            <div style="display:flex; gap:10px;">
                <button type="button" onclick="closeSocialWorkspaceModal()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); box-shadow:none;">Cancel</button>
                <button type="button" onclick="submitSocialWorkspaceId()">Continue</button>
            </div>
        </div>
    </div>

    <div id="authContainer" style="width:100%; height:100vh; display:flex; align-items:center; justify-content:center; background:var(--bg-color); position: relative; z-index: 5;">
        <div class="card" style="width: 100%; max-width: 420px; margin: 0; animation: fadeInScale 0.5s var(--ease-out-expo);">
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:28px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="width:10px; height:10px; background:var(--accent); border-radius:50%; box-shadow: 0 0 16px var(--accent); animation: pulseGlow 2s infinite;"></div>
                    <h2 style="margin:0; font-size: 1.3em; letter-spacing:-0.02em;">Tasko Workspace</h2>
                </div>
                <button type="button" onclick="toggleFeedbackModal()" class="social-btn" style="width:auto; padding:6px 12px; font-size:0.75em; margin:0;">Feedback</button>
            </div>
            
            <div id="loginForm">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;">Sign in to your account</h3>
                
                <button type="button" class="social-btn" onclick="triggerGoogleSignIn()">
                    <svg width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M21.35 11.1h-9.17v2.73h5.51c-.24 1.38-1.54 4.05-5.51 4.05-3.32 0-6.03-2.75-6.03-6.13s2.71-6.13 6.03-6.13c1.89 0 3.15.81 3.87 1.5l2.04-1.99C16.89 3.73 14.88 3 12.18 3 7.12 3 3 7.12 3 12.18s4.12 9.18 9.18 9.18c5.3 0 8.81-3.73 8.81-8.98 0-.62-.07-1.09-.17-1.28z"/></svg>
                    Continue with Google
                </button>
                <button type="button" class="social-btn" onclick="triggerAppleSignIn()">
                    <svg width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M15.97 5.75c.61-.75 1.02-1.8 0.91-2.85-.88.04-1.95.59-2.57 1.34-.57.67-1.07 1.74-.94 2.78 1 .08 1.99-.48 2.6-1.27z"/></svg>
                    Continue with Apple
                </button>

                <div style="display:flex; align-items:center; margin:18px 0; color:var(--subtext); font-size:0.8em;">
                    <div style="flex:1; height:1px; background:var(--border-color);"></div>
                    <span style="padding:0 10px;">OR WITH EMAIL</span>
                    <div style="flex:1; height:1px; background:var(--border-color);"></div>
                </div>

                <input type="email" id="loginEmail" placeholder="name@company.com">
                <input type="password" id="loginPassword" placeholder="••••••••••••">
                <button type="button" onclick="nextLoginStep()">Continue to Workspace ID</button>
                <p style="margin-top:22px; font-size:0.85em; text-align:center; color:var(--subtext);">Don't have an account? <button type="button" onclick="window.toggleAuth('signup')" style="background:none; border:none; color:var(--accent); cursor:pointer; padding:0; font-size:1em; font-weight:500; text-decoration:underline; box-shadow:none; width:auto; display:inline;">Sign up</button></p>
            </div>

            <div id="loginCompanyModal" class="hidden">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;">Enter Workspace ID</h3>
                <p style="font-size:0.85em; color:var(--subtext); margin-bottom:14px;">Please provide your company's Workspace ID to access your dashboard.</p>
                <input type="text" id="loginCompanyId" placeholder="Workspace Company ID">
                <div style="display:flex; gap:10px;">
                    <button type="button" onclick="backToLoginCredentials()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); box-shadow:none;">Back</button>
                    <button type="button" onclick="loginFinal()">Sign In</button>
                </div>
            </div>

            <div id="signupForm" class="hidden">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;">Create a new account</h3>
                
                <button type="button" class="social-btn" onclick="triggerGoogleSignIn()">
                    <svg width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M21.35 11.1h-9.17v2.73h5.51c-.24 1.38-1.54 4.05-5.51 4.05-3.32 0-6.03-2.75-6.03-6.13s2.71-6.13 6.03-6.13c1.89 0 3.15.81 3.87 1.5l2.04-1.99C16.89 3.73 14.88 3 12.18 3 7.12 3 3 7.12 3 12.18s4.12 9.18 9.18 9.18c5.3 0 8.81-3.73 8.81-8.98 0-.62-.07-1.09-.17-1.28z"/></svg>
                    Sign up with Google
                </button>
                <button type="button" class="social-btn" onclick="triggerAppleSignIn()">
                    <svg width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M15.97 5.75c.61-.75 1.02-1.8 0.91-2.85-.88.04-1.95.59-2.57 1.34-.57.67-1.07 1.74-.94 2.78 1 .08 1.99-.48 2.6-1.27z"/></svg>
                    Continue with Apple
                </button>

                <div style="display:flex; align-items:center; margin:18px 0; color:var(--subtext); font-size:0.8em;">
                    <div style="flex:1; height:1px; background:var(--border-color);"></div>
                    <span style="padding:0 10px;">OR WITH EMAIL</span>
                    <div style="flex:1; height:1px; background:var(--border-color);"></div>
                </div>

                <input type="text" id="suName" placeholder="Full Name">
                <input type="email" id="suEmail" placeholder="name@company.com">
                <input type="password" id="suPassword" placeholder="••••••••••••">
                <select id="suRole" onchange="window.toggleCompanyInput()">
                    <option value="Employee">Employee</option>
                    <option value="CEO">CEO (Create Workspace)</option>
                </select>
                <input type="text" id="suCompanyName" placeholder="Company Name" class="hidden">
                <button type="button" onclick="window.nextSignupStep()" style="margin-top:12px;">Continue to Workspace ID</button>
                <p style="margin-top:22px; font-size:0.85em; text-align:center; color:var(--subtext);">Already have an account? <button type="button" onclick="window.toggleAuth('login')" style="background:none; border:none; color:var(--accent); cursor:pointer; padding:0; font-size:1em; font-weight:500; text-decoration:underline; box-shadow:none; width:auto; display:inline;">Sign in</button></p>
            </div>

            <div id="signupCompanyModal" class="hidden">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;" id="signupCompanyModalTitle">Workspace Company ID</h3>
                <p style="font-size:0.85em; color:var(--subtext); margin-bottom:14px;" id="signupCompanyModalSub">Enter the workspace ID provided by your company.</p>
                <input type="text" id="suCompanyId" placeholder="Workspace Company ID">
                <div style="display:flex; gap:10px;">
                    <button type="button" onclick="window.backToSignupCredentials()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); box-shadow:none;">Back</button>
                    <button type="button" onclick="window.registerAccount()">Complete Sign Up</button>
                </div>
            </div>
        </div>
    </div>

    <div id="waitingContainer" class="hidden" style="width:100%; height:100vh; display:flex; align-items:center; justify-content:center; background:var(--bg-color);">
        <div class="card" style="width: 100%; max-width: 420px; margin: 0; text-align:center; padding: 44px 32px; animation: fadeInScale 0.5s var(--ease-out-expo);">
            <div style="width:14px; height:14px; background:#fbbf24; border-radius:50%; margin:0 auto 20px auto; box-shadow: 0 0 20px #fbbf24; animation: pulseGlow 1.4s infinite;"></div>
            <h2 style="margin-bottom:12px;">Waiting for CEO Approval</h2>
            <p style="color:var(--subtext); font-size:0.9em; line-height:1.6; margin-bottom:32px;">Your account has been registered. The workspace CEO needs to review and assign your role/permissions before you can enter.</p>
            <button type="button" onclick="window.logout()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); box-shadow:none;">Sign Out</button>
        </div>
    </div>

    <div id="dashboardContainer" class="hidden" style="display:flex; width:100%; height:100vh;">
        <div class="sidebar">
            <div class="sidebar-brand">
                <div style="width:8px; height:8px; background:var(--accent); border-radius:50%; box-shadow: 0 0 12px var(--accent); animation: pulseGlow 2s infinite;"></div>
                <span id="brandCompanyName">Tasko Workspace</span>
            </div>
            
            <div class="sidebar-menu">
                <button type="button" class="nav-item active" onclick="switchSection('tasksSection', this)">Assigned Tasks</button>
                <button type="button" class="nav-item" onclick="switchSection('routinesSection', this)">Daily Routines</button>
                <button type="button" id="navAssign" class="nav-item ceo-only hidden" onclick="switchSection('assignSection', this)">Assign Task</button>
                <button type="button" id="navRoutinesConfig" class="nav-item ceo-only hidden" onclick="switchSection('routinesConfigSection', this)">Set Daily Routines</button>
                <button type="button" id="navApprovals" class="nav-item ceo-only hidden" onclick="switchSection('approvalsSection', this)">Employee Approvals</button>
                <button type="button" id="navChat" class="nav-item" onclick="switchSection('chatSection', this)">Team Chat</button>
                <button type="button" id="navLogs" class="nav-item" onclick="switchSection('logsSection', this)">Activity Logs</button>
                <button type="button" class="nav-item" onclick="switchSection('settingsSection', this)">Settings</button>
            </div>

            <div style="border-top:1px solid var(--border-color); padding-top:16px; margin-top:auto;">
                <div style="font-size:0.85em; color:var(--text-color); font-weight:600;" id="sidebarUserName">User</div>
                <div style="font-size:0.75em; color:var(--subtext); margin-bottom:4px;" id="sidebarUserRole">Role</div>
                <div style="font-size:0.75em; color:var(--accent); margin-bottom:14px;" id="sidebarUserStance">Stance</div>
                <button type="button" onclick="toggleFeedbackModal()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); padding:8px; font-size:0.8em; margin-bottom:8px; box-shadow:none;">Send Feedback</button>
                <button type="button" onclick="toggleCommandPalette()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--subtext); padding:8px; font-size:0.8em; margin-bottom:8px; box-shadow:none;">Command Palette (⌘K)</button>
                <button type="button" onclick="window.logout()" style="background:rgba(220,38,38,0.12); color:#f87171; border:1px solid rgba(220,38,38,0.25); padding:8px; font-size:0.85em; box-shadow:none;">Logout</button>
            </div>
        </div>

        <div class="main-content">
            <div id="tasksSection" class="section-view">
                <div class="card">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px;">
                        <div>
                            <h2>Assigned Tasks & Routines</h2>
                            <p style="font-size:0.9em; color:var(--subtext); margin:0;">Filter, sort, view Kanban board, and submit proofs for AI verification.</p>
                        </div>
                        <div style="display: flex; gap: 10px;">
                            <button type="button" id="viewListBtn" onclick="setTaskView('list')" style="width: auto; padding: 8px 16px; font-size: 0.85em; background: var(--accent);">List View</button>
                            <button type="button" id="viewKanbanBtn" onclick="setTaskView('kanban')" style="width: auto; padding: 8px 16px; font-size: 0.85em; background: var(--input-bg); border: 1px solid var(--border-color); color: var(--text-color); box-shadow: none;">Kanban Board</button>
                        </div>
                    </div>

                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; background: var(--input-bg); padding: 16px; border-radius: 12px; border: 1px solid var(--border-color); margin-bottom: 20px;">
                        <div>
                            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Search Keywords</label>
                            <input type="text" id="filterSearch" placeholder="Search tasks..." oninput="applyFilters()" style="margin: 4px 0 0 0; padding: 8px 12px;">
                        </div>
                        <div>
                            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Filter Status</label>
                            <select id="filterStatus" onchange="applyFilters()" style="margin: 4px 0 0 0; padding: 8px 12px;">
                                <option value="">All Statuses</option>
                                <option value="Pending">Pending Proof</option>
                                <option value="Completed">Completed</option>
                                <option value="Failed">Failed</option>
                            </select>
                        </div>
                        <div>
                            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Filter Priority</label>
                            <select id="filterPriority" onchange="applyFilters()" style="margin: 4px 0 0 0; padding: 8px 12px;">
                                <option value="">All Priorities</option>
                                <option value="High">High</option>
                                <option value="Medium">Medium</option>
                                <option value="Low">Low</option>
                            </select>
                        </div>
                        <div>
                            <label style="font-size: 0.75em; color: var(--subtext); text-transform: uppercase;">Sort By</label>
                            <select id="sortBy" onchange="applyFilters()" style="margin: 4px 0 0 0; padding: 8px 12px;">
                                <option value="id_desc">Newest First</option>
                                <option value="priority">Priority (High to Low)</option>
                                <option value="deadline">Deadline</option>
                            </select>
                        </div>
                    </div>

                    <div id="taskListViewContainer">
                        <div id="myTasksList">No tasks found.</div>
                    </div>

                    <div id="taskKanbanViewContainer" class="hidden">
                        <div class="kanban-grid">
                            <div class="kanban-col" ondragover="allowDrop(event)" ondrop="dropTask(event, 'Pending')">
                                <div class="kanban-col-header">Pending Proof <span id="countPending" style="background:var(--card-bg); padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span></div>
                                <div class="kanban-cards" id="kanbanPending"></div>
                            </div>
                            <div class="kanban-col" ondragover="allowDrop(event)" ondrop="dropTask(event, 'Completed')">
                                <div class="kanban-col-header">Completed <span id="countCompleted" style="background:var(--card-bg); padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span></div>
                                <div class="kanban-cards" id="kanbanCompleted"></div>
                            </div>
                            <div class="kanban-col" ondragover="allowDrop(event)" ondrop="dropTask(event, 'Failed')">
                                <div class="kanban-col-header">Failed <span id="countFailed" style="background:var(--card-bg); padding:2px 8px; border-radius:10px; font-size:0.8em;">0</span></div>
                                <div class="kanban-cards" id="kanbanFailed"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="routinesSection" class="section-view hidden">
                <div class="card">
                    <h2>Daily Recurring Routines</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">These routines reset every single day. Complete them daily!</p>
                    <div id="dailyRoutinesList" style="margin-top:20px;">No daily routines set for this workspace.</div>
                </div>
            </div>

            <div id="assignSection" class="section-view hidden">
                <div class="card">
                    <h2>Assign One-Time Task</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Dispatch new instructions and deadlines to active team members.</p>
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Task Title & Instructions</label>
                    <input type="text" id="taskTitle" placeholder="e.g. Audit Quarterly Financials">
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Assignee</label>
                    <select id="taskAssigneeSelect">
                        <option value="">Select Employee</option>
                    </select>
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Strict Deadline</label>
                    <input type="datetime-local" id="taskDeadline">
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Priority</label>
                    <select id="taskPriority">
                        <option value="Low">Low Priority</option>
                        <option value="Medium" selected>Medium Priority</option>
                        <option value="High">High Priority</option>
                    </select>
                    
                    <button type="button" onclick="createTask()" style="margin-top:12px;">Assign Task to Team Member</button>
                </div>
            </div>

            <div id="routinesConfigSection" class="section-view hidden">
                <div class="card">
                    <h2>Configure Auto-Daily Routines</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Set up mandatory tasks that your team must perform and check off every single day.</p>
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Routine Title & Instructions</label>
                    <input type="text" id="routineTitle" placeholder="e.g. Open Shop & Verify Inventory">
                    
                    <label style="font-size:0.85em; color:var(--subtext);">Assignee</label>
                    <select id="routineAssigneeSelect">
                        <option value="">Select Employee</option>
                    </select>
                    
                    <button type="button" onclick="createDailyRoutine()" style="margin-top:12px;">Add Auto-Daily Routine</button>

                    <h4 style="margin-top:28px; font-size:0.95em; color:var(--subtext);">Active Daily Routines</h4>
                    <div id="configRoutinesList">No routines configured.</div>
                </div>
            </div>

            <div id="approvalsSection" class="section-view hidden">
                <div class="card">
                    <h2>Employee Approvals & Role Management</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Review pending join requests, assign custom stances/roles, and configure granular permissions.</p>
                    
                    <h4 style="margin-top:24px; font-size:0.95em; color:var(--subtext);">Pending Requests</h4>
                    <div id="pendingUsersList" style="margin-bottom:24px;">No pending users.</div>

                    <h4 style="margin-top:24px; font-size:0.95em; color:var(--subtext);">Active Workspace Employees</h4>
                    <div id="employeesManageList">No employees in workspace.</div>
                </div>
            </div>

            <div id="chatSection" class="section-view hidden">
                <div class="card" style="height:calc(100vh - 120px); display:flex; flex-direction:column;">
                    <h2>Live Workspace Chat</h2>
                    <div id="chatBox" class="chat-box" style="flex:1;"></div>
                    <div style="display:flex; gap:12px; margin-top:auto;">
                        <input type="text" id="chatInput" placeholder="Type a message to the team..." onkeydown="if(event.key==='Enter') sendChatMessage()" style="margin:0;">
                        <button type="button" onclick="sendChatMessage()" style="width: auto; margin:0; padding:0 28px;">Send</button>
                    </div>
                </div>
            </div>

            <div id="logsSection" class="section-view hidden">
                <div class="card">
                    <h2>Workspace Activity Log</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Complete audit trail of assignments, verifications, and approvals.</p>
                    <table>
                        <thead>
                            <tr><th>Time</th><th>Action</th></tr>
                        </thead>
                        <tbody id="historyTableBody">
                            <tr><td colspan="2" style="color:var(--subtext);">No history recorded yet.</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div id="settingsSection" class="section-view hidden">
                <div class="card" style="max-width: 600px;">
                    <h2>Workspace Settings</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Customize your workspace appearance and preferences.</p>
                    
                    <div style="margin-top:24px;">
                        <label style="font-size:0.85em; color:var(--subtext);">Workspace Company Name</label>
                        <div style="display:flex; gap:12px;">
                            <input type="text" id="settingsCompanyName" placeholder="MyCorp">
                            <button type="button" onclick="updateCompanyName()" style="width:auto; margin:8px 0 18px 0; padding:0 20px;">Save</button>
                        </div>
                    </div>

                    <div style="margin-top:12px;">
                        <label style="font-size:0.85em; color:var(--subtext);">Workspace ID (Read-only)</label>
                        <input type="text" id="settingsCompanyId" readonly style="opacity:0.7; cursor:not-allowed;">
                    </div>

                    <div style="margin-top:24px; border-top:1px solid var(--border-color); padding-top:24px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:10px;">Appearance Theme</label>
                        <button type="button" onclick="toggleTheme()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); width:auto; padding:10px 18px; box-shadow:none;">Toggle Dark / Light Mode</button>
                    </div>

                    <div style="margin-top:24px; border-top:1px solid var(--border-color); padding-top:24px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:10px;">Workspace Auto-Refresh Rate</label>
                        <select id="settingsRefreshRate" onchange="updateRefreshRateSetting()">
                            <option value="3">Fast (3 seconds)</option>
                            <option value="10" selected>Balanced (10 seconds)</option>
                            <option value="30">Slow (30 seconds)</option>
                        </select>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const GOOGLE_CLIENT_ID = {{ google_client_id | tojson }};
        const APPLE_CLIENT_ID = {{ apple_client_id | tojson }};

        let currentUser = JSON.parse(localStorage.getItem("tasko_user")) || null;
        let authToken = localStorage.getItem("tasko_token") || null;
        let currentTheme = localStorage.getItem("tasko_theme") || "dark";
        let savedRefreshRate = localStorage.getItem("tasko_refresh_rate") || "10";
        let workspaceRefreshInterval = null;
        let approvalCheckInterval = null;
        let chatPollInterval = null;
        let pendingOAuthData = null;
        let selectedProofFiles = {};
        let cachedTasks = [];
        let currentTaskView = "list";

        function escapeHtml(str) {
            if (!str) return "";
            return String(str)
                .replace(/&/g, "\\&")
                .replace(/</g, "\\<")
                .replace(/>/g, "\\>")
                .replace(/"/g, "\\"")
                .replace(/'/g, "\\'");
        }

        const commandList = [
            { title: "Send Workspace Feedback", action: () => { toggleFeedbackModal(); closeCommandPalette(); } },
            { title: "Go to Assigned Tasks", action: () => { switchSection("tasksSection", document.querySelector(".sidebar-menu button")); closeCommandPalette(); } },
            { title: "Go to Daily Routines", action: () => { switchSection("routinesSection", document.querySelectorAll(".sidebar-menu button")[1]); closeCommandPalette(); } },
            { title: "Go to Team Chat", action: () => { switchSection("chatSection", document.getElementById("navChat")); closeCommandPalette(); } },
            { title: "Go to Settings", action: () => { const btns = document.querySelectorAll(".sidebar-menu button"); switchSection("settingsSection", btns[btns.length - 1]); closeCommandPalette(); } },
            { title: "Toggle Dark / Light Theme", action: () => { toggleTheme(); closeCommandPalette(); } },
            { title: "Switch to Kanban Board View", action: () => { setTaskView("kanban"); switchSection("tasksSection", document.querySelector(".sidebar-menu button")); closeCommandPalette(); } },
            { title: "Switch to List View", action: () => { setTaskView("list"); switchSection("tasksSection", document.querySelector(".sidebar-menu button")); closeCommandPalette(); } }
        ];

        async function apiFetch(url, options = {}) {
            options.headers = options.headers || {};
            if (authToken) {
                options.headers["Authorization"] = `Bearer ${authToken}`;
            }
            const res = await fetch(url, options);
            if (res.status === 401 && authToken) {
                logout();
            }
            return res;
        }

        window.addEventListener("DOMContentLoaded", () => {
            if (currentTheme === "light") {
                document.body.classList.add("light-theme");
            }
            const refreshSelect = document.getElementById("settingsRefreshRate");
            if (refreshSelect) refreshSelect.value = savedRefreshRate;

            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get("search")) document.getElementById("filterSearch").value = urlParams.get("search");
            if (urlParams.get("status")) document.getElementById("filterStatus").value = urlParams.get("status");
            if (urlParams.get("priority")) document.getElementById("filterPriority").value = urlParams.get("priority");
            if (urlParams.get("sort")) document.getElementById("sortBy").value = urlParams.get("sort");
            if (urlParams.get("view") === "kanban") setTaskView("kanban");

            const cmdInput = document.getElementById("cmdInput");
            if (cmdInput) {
                cmdInput.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") {
                        const selected = document.querySelector(".cmd-item.selected");
                        if (selected) selected.click();
                    }
                });
            }

            window.addEventListener("keydown", (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
                    e.preventDefault();
                    toggleCommandPalette();
                } else if (e.key === "Escape") {
                    closeCommandPalette();
                    closeSocialWorkspaceModal();
                    document.getElementById("feedbackModal").classList.remove("active");
                }
            });

            if (typeof google !== "undefined" && GOOGLE_CLIENT_ID) {
                google.accounts.id.initialize({
                    client_id: GOOGLE_CLIENT_ID,
                    callback: handleGoogleOAuthCredential
                });
            }

            try {
                if (typeof AppleID !== "undefined" && APPLE_CLIENT_ID) {
                    AppleID.auth.init({
                        clientId: APPLE_CLIENT_ID,
                        scope: "name email",
                        redirectURI: window.location.origin + "/app",
                        usePopup: true
                    });
                }
            } catch (e) {
                console.log("AppleID init note:", e);
            }

            if (currentUser && authToken) {
                checkLoginState();
                if (!chatPollInterval) {
                    chatPollInterval = setInterval(loadWorkspaceChat, 3000);
                }
            } else if (currentUser && !authToken) {
                logout();
            }
        });

        window.toggleFeedbackModal = function() {
            document.getElementById("feedbackModal").classList.toggle("active");
        };

        window.submitFeedbackDraft = function() {
            const platform = document.getElementById("feedbackPlatformSelect").value;
            const feedback = document.getElementById("feedbackText").value.trim();
            const subject = encodeURIComponent("Tasko Workspace Feedback");
            const body = encodeURIComponent(feedback || "Hello Tasko Team,\n\nHere is my feedback regarding the workspace:");

            let targetUrl = "";
            switch (platform) {
                case "gmail":
                case "google_workspace":
                    targetUrl = `https://mail.google.com/mail/?view=cm&fs=1&to=taskofeedback@gmail.com&su=${subject}&body=${body}`;
                    break;
                case "outlook":
                case "m365":
                case "hotmail":
                    targetUrl = `https://outlook.office.com/mail/deeplink/compose?to=taskofeedback@gmail.com&subject=${subject}&body=${body}`;
                    break;
                case "yahoo":
                    targetUrl = `https://compose.mail.yahoo.com/?to=taskofeedback@gmail.com&subject=${subject}&body=${body}`;
                    break;
                case "aol":
                    targetUrl = `https://mail.aol.com/webmail/compose?to=taskofeedback@gmail.com&subject=${subject}&body=${body}`;
                    break;
                case "zoho_personal":
                case "zoho_business":
                    targetUrl = `https://mail.zoho.com/zm/#compose?to=taskofeedback@gmail.com&subject=${subject}&body=${body}`;
                    break;
                case "fastmail":
                    targetUrl = `https://app.fastmail.com/mail/compose?to=taskofeedback@gmail.com&subject=${subject}&body=${body}`;
                    break;
                default:
                    targetUrl = `mailto:taskofeedback@gmail.com?subject=${subject}&body=${body}`;
                    break;
            }

            toggleFeedbackModal();
            window.open(targetUrl, "_blank");
            showToast("Opening draft in your selected email platform!");
        };

        window.toggleCommandPalette = function() {
            const modal = document.getElementById("cmdPaletteModal");
            modal.classList.toggle("active");
            if (modal.classList.contains("active")) {
                const input = document.getElementById("cmdInput");
                input.value = "";
                filterCommands();
                input.focus();
            }
        };

        window.closeCommandPalette = function() {
            document.getElementById("cmdPaletteModal").classList.remove("active");
        };

        window.filterCommands = function() {
            const query = document.getElementById("cmdInput").value.toLowerCase();
            const filtered = commandList.filter(c => c.title.toLowerCase().includes(query));
            const listEl = document.getElementById("cmdResultsList");
            if (filtered.length === 0) {
                listEl.innerHTML = `<div style="padding:16px; text-align:center; color:var(--subtext);">No matching actions found.</div>`;
                return;
            }
            listEl.innerHTML = filtered.map((cmd, idx) => `
                <div class="cmd-item ${idx === 0 ? "selected" : ""}" onclick="executeCommand(${commandList.indexOf(cmd)})">
                    <span>${escapeHtml(cmd.title)}</span>
                    <span style="font-size:0.75em; color:var(--subtext);">Press Enter</span>
                </div>
            `).join("");
        };

        window.executeCommand = function(index) {
            if (commandList[index]) {
                commandList[index].action();
            }
        };

        window.setTaskView = function(view) {
            currentTaskView = view;
            const listBtn = document.getElementById("viewListBtn");
            const kanbanBtn = document.getElementById("viewKanbanBtn");
            const listContainer = document.getElementById("taskListViewContainer");
            const kanbanContainer = document.getElementById("taskKanbanViewContainer");

            if (view === "kanban") {
                kanbanBtn.style.background = "var(--accent)";
                kanbanBtn.style.color = "#fff";
                kanbanBtn.style.border = "none";
                listBtn.style.background = "var(--input-bg)";
                listBtn.style.color = "var(--text-color)";
                listBtn.style.border = "1px solid var(--border-color)";
                listContainer.classList.add("hidden");
                kanbanContainer.classList.remove("hidden");
            } else {
                listBtn.style.background = "var(--accent)";
                listBtn.style.color = "#fff";
                listBtn.style.border = "none";
                kanbanBtn.style.background = "var(--input-bg)";
                kanbanBtn.style.color = "var(--text-color)";
                kanbanBtn.style.border = "1px solid var(--border-color)";
                kanbanContainer.classList.add("hidden");
                listContainer.classList.remove("hidden");
            }
            updateURLParams();
            renderTasks(cachedTasks);
        };

        window.allowDrop = function(ev) {
            ev.preventDefault();
        };

        window.dragTaskStart = function(ev, taskId) {
            ev.dataTransfer.setData("text/plain", String(taskId));
        };

        window.dropTask = async function(ev, newStatus) {
            ev.preventDefault();
            const taskId = ev.dataTransfer.getData("text/plain");
            if (!taskId) return;
            showLoader("Updating task status...");
            const res = await apiFetch("/api/tasks/update-status", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ task_id: taskId, status: newStatus })
            });
            hideLoader();
            if (res.ok) {
                showToast(`Task moved to ${newStatus}!`);
                loadDashboardData();
            } else {
                const err = await res.json();
                showToast(err.error || "Failed to update status", "error");
            }
        };

        window.applyFilters = function() {
            updateURLParams();
            renderTasks(cachedTasks);
        };

        function updateURLParams() {
            const search = document.getElementById("filterSearch").value;
            const status = document.getElementById("filterStatus").value;
            const priority = document.getElementById("filterPriority").value;
            const sort = document.getElementById("sortBy").value;

            const params = new URLSearchParams();
            if (search) params.set("search", search);
            if (status) params.set("status", status);
            if (priority) params.set("priority", priority);
            if (sort && sort !== "id_desc") params.set("sort", sort);
            if (currentTaskView === "kanban") params.set("view", "kanban");

            const queryStr = params.toString();
            const newRelativePathQuery = window.location.pathname + (queryStr ? "?" + queryStr : "");
            history.replaceState(null, "", newRelativePathQuery);
        }

        function showLoader(text = "Authenticating with Workspace...") {
            document.getElementById("loaderText").innerText = text;
            document.getElementById("globalLoader").classList.add("active");
        }

        function hideLoader() {
            document.getElementById("globalLoader").classList.remove("active");
        }

        function showToast(message, type = "success") {
            const container = document.getElementById("toastContainer");
            const toast = document.createElement("div");
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = "toastOut 0.35s var(--ease-out-expo) forwards";
                setTimeout(() => toast.remove(), 350);
            }, 3000);
        }

        window.handleFileSelect = function(taskId, input) {
            if (input.files && input.files[0]) {
                selectedProofFiles[taskId] = input.files[0];
                showToast("Image selected successfully!", "info");
            }
        };

        window.triggerGoogleSignIn = function() {
            if (!GOOGLE_CLIENT_ID) {
                showToast("Google Client ID is missing. Set GOOGLE_CLIENT_ID in Railway variables.", "error");
                return;
            }
            if (typeof google !== "undefined") {
                google.accounts.id.prompt((notification) => {
                    if (notification.isNotDisplayed()) {
                        showToast("Google sign-in popup was blocked by browser settings.", "error");
                    }
                });
            } else {
                showToast("Google SDK is still loading.", "info");
            }
        };

        async function handleGoogleOAuthCredential(response) {
            showLoader("Verifying Google OAuth token...");
            const res = await fetch("/api/auth/google", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ credential: response.credential })
            });
            const data = await res.json();
            hideLoader();
            if (res.ok) {
                pendingOAuthData = data;
                openSocialWorkspaceModal();
            } else {
                showToast(data.error || "Google authentication failed", "error");
            }
        }

        window.triggerAppleSignIn = async function() {
            try {
                showLoader("Connecting to Apple ID...");
                const data = await AppleID.auth.signIn();
                const idToken = data.authorization.id_token;
                const res = await fetch("/api/auth/apple", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id_token: idToken })
                });
                const result = await res.json();
                hideLoader();
                if (res.ok) {
                    pendingOAuthData = result;
                    openSocialWorkspaceModal();
                } else {
                    showToast(result.error || "Apple authentication failed", "error");
                }
            } catch (error) {
                hideLoader();
                showToast("Apple Sign-In cancelled or unavailable.", "error");
            }
        };

        function openSocialWorkspaceModal() {
            document.getElementById("socialModalTitle").innerText = `Link ${pendingOAuthData.provider} Account`;
            document.getElementById("socialWorkspaceModal").classList.remove("hidden");
        }

        window.closeSocialWorkspaceModal = function() {
            document.getElementById("socialWorkspaceModal").classList.add("hidden");
        };

        window.submitSocialWorkspaceId = async function() {
            const company_id = document.getElementById("socialWorkspaceIdInput").value.trim();
            if (!company_id) {
                showToast("Workspace ID is required.", "error");
                return;
            }
            closeSocialWorkspaceModal();
            showLoader(`Authenticating with ${pendingOAuthData.provider}...`);

            const res = await fetch("/api/social-login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    oauth_token: pendingOAuthData.oauth_token,
                    company_id
                })
            });
            hideLoader();
            const result = await res.json();
            if (res.ok) {
                currentUser = result.user;
                authToken = result.token;
                localStorage.setItem("tasko_user", JSON.stringify(currentUser));
                localStorage.setItem("tasko_token", authToken);
                showToast(`Successfully authenticated with ${pendingOAuthData.provider}!`);
                checkLoginState();
            } else {
                showToast(result.error, "error");
            }
        };

        window.updateRefreshRateSetting = function() {
            savedRefreshRate = document.getElementById("settingsRefreshRate").value;
            localStorage.setItem("tasko_refresh_rate", savedRefreshRate);
            restartAutoRefreshInterval();
        };

        function restartAutoRefreshInterval() {
            if (workspaceRefreshInterval) clearInterval(workspaceRefreshInterval);
            const intervalMs = parseInt(savedRefreshRate, 10) * 1000;
            workspaceRefreshInterval = setInterval(() => {
                if (!currentUser || !authToken) return;
                loadDashboardData();
                if (currentUser.role === "CEO" || (currentUser.permissions && currentUser.permissions.includes("handle_approvals"))) {
                    loadPendingUsers();
                    loadEmployeesManagement();
                }
            }, intervalMs);
        }

        window.toggleTheme = function() {
            document.body.classList.toggle("light-theme");
            currentTheme = document.body.classList.contains("light-theme") ? "light" : "dark";
            localStorage.setItem("tasko_theme", currentTheme);
        };

        window.switchSection = function(sectionId, btnElement) {
            document.querySelectorAll(".section-view").forEach(el => el.classList.add("hidden"));
            document.getElementById(sectionId).classList.remove("hidden");
            document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
            if (btnElement) btnElement.classList.add("active");
        };

        window.toggleAuth = function(formType) {
            const loginForm = document.getElementById("loginForm");
            const signupForm = document.getElementById("signupForm");
            const loginCompanyModal = document.getElementById("loginCompanyModal");
            const signupCompanyModal = document.getElementById("signupCompanyModal");

            if (!loginForm || !signupForm) return;

            loginCompanyModal.classList.add("hidden");
            signupCompanyModal.classList.add("hidden");

            if (formType === "signup") {
                loginForm.classList.add("hidden");
                signupForm.classList.remove("hidden");
            } else {
                signupForm.classList.add("hidden");
                loginForm.classList.remove("hidden");
            }
        };

        window.toggleCompanyInput = function() {
            const role = document.getElementById("suRole").value;
            const compInput = document.getElementById("suCompanyName");
            if (role === "CEO") {
                compInput.classList.remove("hidden");
            } else {
                compInput.classList.add("hidden");
            }
        };

        window.nextLoginStep = function() {
            const email = document.getElementById("loginEmail").value.trim();
            const password = document.getElementById("loginPassword").value;
            if (!email || !password) {
                showToast("Please enter both email and password.", "error");
                return;
            }
            document.getElementById("loginForm").classList.add("hidden");
            document.getElementById("loginCompanyModal").classList.remove("hidden");
        };

        window.backToLoginCredentials = function() {
            document.getElementById("loginCompanyModal").classList.add("hidden");
            document.getElementById("loginForm").classList.remove("hidden");
        };

        window.nextSignupStep = function() {
            const name = document.getElementById("suName").value.trim();
            const email = document.getElementById("suEmail").value.trim();
            const password = document.getElementById("suPassword").value;
            const role = document.getElementById("suRole").value;
            const companyName = document.getElementById("suCompanyName").value.trim();

            if (!name || !email || !password) {
                showToast("Please fill out all mandatory fields.", "error");
                return;
            }
            if (role === "CEO" && !companyName) {
                showToast("Company name cannot be blank when creating a workspace.", "error");
                return;
            }

            document.getElementById("signupForm").classList.add("hidden");
            document.getElementById("signupCompanyModal").classList.remove("hidden");

            const companyIdInput = document.getElementById("suCompanyId");
            if (role === "CEO") {
                document.getElementById("signupCompanyModalTitle").innerText = "Workspace Company ID (Generated)";
                document.getElementById("signupCompanyModalSub").innerText = "A new workspace ID will be created for your company.";
                companyIdInput.value = String(Math.floor(1000000000 + Math.random() * 9000000000));
                companyIdInput.readOnly = true;
                companyIdInput.style.opacity = "0.7";
            } else {
                document.getElementById("signupCompanyModalTitle").innerText = "Workspace Company ID";
                document.getElementById("signupCompanyModalSub").innerText = "Enter the workspace ID provided by your company.";
                companyIdInput.value = "";
                companyIdInput.readOnly = false;
                companyIdInput.style.opacity = "1";
            }
        };

        window.backToSignupCredentials = function() {
            document.getElementById("signupCompanyModal").classList.add("hidden");
            document.getElementById("signupForm").classList.remove("hidden");
        };

        window.registerAccount = async function() {
            const signupData = {
                name: document.getElementById("suName").value.trim(),
                email: document.getElementById("suEmail").value.trim(),
                password: document.getElementById("suPassword").value,
                role: document.getElementById("suRole").value,
                company_name: document.getElementById("suCompanyName").value.trim(),
                company_id: document.getElementById("suCompanyId").value.trim()
            };
            if (signupData.role === "Employee" && !signupData.company_id) {
                showToast("Workspace Company ID is required for employee sign up.", "error");
                return;
            }
            showLoader("Creating your account & workspace...");
            const res = await fetch("/api/signup", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(signupData)
            });
            hideLoader();
            const result = await res.json();
            if (res.ok) {
                currentUser = result.user;
                authToken = result.token;
                localStorage.setItem("tasko_user", JSON.stringify(currentUser));
                localStorage.setItem("tasko_token", authToken);
                showToast("Account created successfully!");
                checkLoginState();
            } else {
                showToast(result.error, "error");
            }
        };

        window.loginFinal = async function() {
            const data = {
                email: document.getElementById("loginEmail").value.trim(),
                password: document.getElementById("loginPassword").value,
                company_id: document.getElementById("loginCompanyId").value.trim()
            };
            if (!data.company_id) {
                showToast("Please enter the Workspace ID.", "error");
                return;
            }
            showLoader("Signing into workspace...");
            const res = await fetch("/api/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            hideLoader();
            const result = await res.json();
            if (res.ok) {
                currentUser = result.user;
                authToken = result.token;
                localStorage.setItem("tasko_user", JSON.stringify(currentUser));
                localStorage.setItem("tasko_token", authToken);
                showToast("Logged in successfully!");
                checkLoginState();
            } else {
                showToast(result.error, "error");
            }
        };

        window.logout = function() {
            currentUser = null;
            authToken = null;
            localStorage.removeItem("tasko_user");
            localStorage.removeItem("tasko_token");
            if (workspaceRefreshInterval) clearInterval(workspaceRefreshInterval);
            if (approvalCheckInterval) clearInterval(approvalCheckInterval);
            if (chatPollInterval) {
                clearInterval(chatPollInterval);
                chatPollInterval = null;
            }
            document.getElementById("authContainer").style.display = "flex";
            document.getElementById("waitingContainer").classList.add("hidden");
            document.getElementById("dashboardContainer").classList.add("hidden");
            toggleAuth("login");
        };

        async function checkLoginState() {
            if (!currentUser || !authToken) return;

            const statusRes = await apiFetch("/api/check-status");
            if (!statusRes.ok) return;
            const statusData = await statusRes.json();

            if (statusData.status === "Kicked") {
                showToast("You have been removed from the workspace by the CEO.", "error");
                logout();
                return;
            }

            if (currentUser.role === "Employee") {
                if (statusData.status === "Pending") {
                    document.getElementById("authContainer").style.display = "none";
                    document.getElementById("dashboardContainer").classList.add("hidden");
                    document.getElementById("waitingContainer").classList.remove("hidden");
                    if (!approvalCheckInterval) {
                        approvalCheckInterval = setInterval(checkLoginState, 5000);
                    }
                    return;
                } else if (statusData.status === "Approved") {
                    if (approvalCheckInterval) clearInterval(approvalCheckInterval);
                    currentUser.status = "Approved";
                    currentUser.company_name = statusData.company_name;
                    currentUser.stance = statusData.stance;
                    currentUser.permissions = statusData.permissions || [];
                    localStorage.setItem("tasko_user", JSON.stringify(currentUser));
                }
            }

            document.getElementById("authContainer").style.display = "none";
            document.getElementById("waitingContainer").classList.add("hidden");
            const dash = document.getElementById("dashboardContainer");
            dash.classList.remove("hidden");

            document.getElementById("sidebarUserName").innerText = currentUser.name;
            document.getElementById("sidebarUserRole").innerText = currentUser.role;
            document.getElementById("sidebarUserStance").innerText = currentUser.stance ? `Stance: ${currentUser.stance}` : "";
            document.getElementById("brandCompanyName").innerText = currentUser.company_name || "Tasko Workspace";

            document.getElementById("settingsCompanyName").value = currentUser.company_name || "MyCorp";
            document.getElementById("settingsCompanyId").value = currentUser.company_id;

            const isCeo = currentUser.role === "CEO";
            const permissions = currentUser.permissions || [];

            document.querySelectorAll(".ceo-only").forEach(el => {
                if (isCeo) el.classList.remove("hidden");
                else el.classList.add("hidden");
            });

            if (!isCeo) {
                document.getElementById("navAssign").classList.toggle("hidden", !permissions.includes("assign_tasks"));
                document.getElementById("navRoutinesConfig").classList.toggle("hidden", !permissions.includes("config_routines"));
                document.getElementById("navApprovals").classList.toggle("hidden", !permissions.includes("handle_approvals"));
                document.getElementById("navLogs").classList.toggle("hidden", !permissions.includes("view_logs"));
            }

            if (isCeo || permissions.includes("handle_approvals") || permissions.includes("assign_tasks") || permissions.includes("config_routines")) {
                loadEmployeesDropdown();
            }
            if (isCeo || permissions.includes("handle_approvals")) {
                loadPendingUsers();
                loadEmployeesManagement();
            }
            loadDashboardData();
            loadWorkspaceChat();
            if (!chatPollInterval) {
                chatPollInterval = setInterval(loadWorkspaceChat, 3000);
            }
            restartAutoRefreshInterval();
        }

        window.updateCompanyName = async function() {
            const newName = document.getElementById("settingsCompanyName").value.trim();
            if (!newName) {
                showToast("Company name cannot be empty.", "error");
                return;
            }
            showLoader("Updating company name...");
            const res = await apiFetch("/api/admin/update-company", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ company_name: newName })
            });
            hideLoader();
            if (res.ok) {
                currentUser.company_name = newName;
                localStorage.setItem("tasko_user", JSON.stringify(currentUser));
                document.getElementById("brandCompanyName").innerText = newName;
                showToast("Company name updated successfully!");
            } else {
                const err = await res.json();
                showToast(err.error || "Unauthorized", "error");
            }
        };

        async function loadPendingUsers() {
            const res = await apiFetch("/api/admin/pending-users");
            if (!res.ok) return;
            const data = await res.json();
            const container = document.getElementById("pendingUsersList");
            if (data.pending_users.length === 0) {
                container.innerHTML = "<span style='color:var(--subtext);'>No pending users.</span>";
                return;
            }
            container.innerHTML = data.pending_users.map(u => {
                const key = u.email.replace(/[^a-zA-Z0-9]/g, "_");
                return `
                <div style="background:var(--input-bg); padding:16px; margin:12px 0; border-radius:12px; border:1px solid var(--border-color);">
                    <div style="margin-bottom:8px;"><strong>${escapeHtml(u.name)}</strong> (${escapeHtml(u.email)})</div>
                    <label style="font-size:0.8em; color:var(--subtext);">Select Stance / Type Custom Role:</label>
                    <select id="stanceSelect_${key}" onchange="document.getElementById('customStance_${key}').classList.toggle('hidden', this.value!=='Custom')" style="margin-bottom:8px;">
                        <option value="Team Member">Team Member</option>
                        <option value="Lead Developer">Lead Developer</option>
                        <option value="Operations Manager">Operations Manager</option>
                        <option value="Marketing Specialist">Marketing Specialist</option>
                        <option value="Custom">Custom Role...</option>
                    </select>
                    <input type="text" id="customStance_${key}" placeholder="Enter custom stance..." class="hidden" style="margin-bottom:10px;">
                    <label style="font-size:0.8em; color:var(--subtext); display:block; margin-bottom:6px;">Permissions:</label>
                    <div style="display:flex; flex-direction:column; gap:6px; font-size:0.85em; margin-bottom:14px;">
                        <label><input type="checkbox" id="perm_approvals_${key}"> Handle Employee Approvals</label>
                        <label><input type="checkbox" id="perm_kick_${key}"> Kick Employees</label>
                        <label><input type="checkbox" id="perm_routines_${key}"> Set Daily Routines</label>
                        <label><input type="checkbox" id="perm_stances_${key}"> Modify Stances & Roles</label>
                        <label><input type="checkbox" id="perm_assign_${key}" checked> Assign Tasks</label>
                        <label><input type="checkbox" id="perm_logs_${key}" checked> View Activity Logs</label>
                    </div>
                    <button type="button" onclick="window.approveUser('${escapeHtml(u.email)}')" style="width:auto; padding:8px 16px; font-size:0.85em;">Approve Employee</button>
                </div>`;
            }).join("");
        }

        window.approveUser = async function(email) {
            const emailKey = email.replace(/[^a-zA-Z0-9]/g, "_");
            const stanceDropdown = document.getElementById(`stanceSelect_${emailKey}`).value;
            let stance = stanceDropdown === "Custom" ? document.getElementById(`customStance_${emailKey}`).value.trim() : stanceDropdown;
            if (!stance) stance = "Team Member";

            const permissions = [];
            if (document.getElementById(`perm_approvals_${emailKey}`).checked) permissions.push("handle_approvals");
            if (document.getElementById(`perm_kick_${emailKey}`).checked) permissions.push("kick_employees");
            if (document.getElementById(`perm_routines_${emailKey}`).checked) permissions.push("config_routines");
            if (document.getElementById(`perm_stances_${emailKey}`).checked) permissions.push("modify_stances");
            if (document.getElementById(`perm_assign_${emailKey}`).checked) permissions.push("assign_tasks");
            if (document.getElementById(`perm_logs_${emailKey}`).checked) permissions.push("view_logs");

            showLoader("Approving employee...");
            const res = await apiFetch("/api/admin/approve-user", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, stance, permissions })
            });
            hideLoader();
            if (res.ok) {
                showToast("Employee approved successfully!");
                loadPendingUsers();
                loadEmployeesManagement();
                loadEmployeesDropdown();
            } else {
                const err = await res.json();
                showToast(err.error || "Approval failed", "error");
            }
        };

        async function loadEmployeesManagement() {
            const res = await apiFetch("/api/admin/employees-list");
            if (!res.ok) return;
            const data = await res.json();
            const container = document.getElementById("employeesManageList");
            if (data.employees.length === 0) {
                container.innerHTML = "<span style='color:var(--subtext);'>No active employees.</span>";
                return;
            }
            container.innerHTML = data.employees.map(e => `
                <div style="display:flex; justify-content:space-between; align-items:center; padding:12px 0; border-bottom:1px solid var(--border-color);">
                    <div>
                        <span>${escapeHtml(e.name)} (${escapeHtml(e.email)}) - <b style="color:var(--accent);">${escapeHtml(e.stance || e.role)}</b></span>
                        <div style="font-size:0.75em; color:var(--subtext); margin-top:2px;">Permissions: ${escapeHtml((e.permissions || []).join(", ") || "None")}</div>
                    </div>
                    ${(e.role !== "CEO" && (currentUser.role === "CEO" || (currentUser.permissions && currentUser.permissions.includes("kick_employees")))) ? `<button type="button" onclick="window.kickEmployee('${escapeHtml(e.email)}')" style="width:auto; padding:6px 14px; font-size:0.85em; background:rgba(220,38,38,0.15); color:#f87171; border:1px solid rgba(220,38,38,0.3); box-shadow:none;">Kick</button>` : ""}
                </div>
            `).join("");
        }

        window.kickEmployee = async function(email) {
            if (!confirm("Are you sure you want to kick this employee?")) return;
            showLoader("Removing employee...");
            const res = await apiFetch("/api/admin/kick-employee", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email })
            });
            hideLoader();
            if (res.ok) {
                showToast("Employee removed from workspace.", "info");
                loadEmployeesManagement();
                loadEmployeesDropdown();
                loadDashboardData();
            } else {
                const err = await res.json();
                showToast(err.error || "Failed to kick employee.", "error");
            }
        };

        async function loadEmployeesDropdown() {
            const res = await apiFetch("/api/admin/employees-list");
            if (!res.ok) return;
            const data = await res.json();
            const opts = '<option value="">Select Employee</option>' + data.employees.map(e => `<option value="${escapeHtml(e.name)}">${escapeHtml(e.name)} (${escapeHtml(e.stance || e.role)})</option>`).join("");
            document.getElementById("taskAssigneeSelect").innerHTML = opts;
            document.getElementById("routineAssigneeSelect").innerHTML = opts;
        }

        window.createTask = async function() {
            const data = {
                title: document.getElementById("taskTitle").value.trim(),
                assigned_to: document.getElementById("taskAssigneeSelect").value,
                deadline: document.getElementById("taskDeadline").value,
                priority: document.getElementById("taskPriority").value
            };
            if (!data.title || !data.assigned_to) {
                showToast("Please fill out task title and select an assignee.", "error");
                return;
            }
            showLoader("Assigning task...");
            const res = await apiFetch("/api/tasks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            hideLoader();
            if (res.ok) {
                document.getElementById("taskTitle").value = "";
                document.getElementById("taskDeadline").value = "";
                showToast("Task assigned successfully!");
                loadDashboardData();
            } else {
                const err = await res.json();
                showToast(err.error || "Failed to assign task", "error");
            }
        };

        window.createDailyRoutine = async function() {
            const data = {
                title: document.getElementById("routineTitle").value.trim(),
                assigned_to: document.getElementById("routineAssigneeSelect").value
            };
            if (!data.title || !data.assigned_to) {
                showToast("Please fill out routine title and select an assignee.", "error");
                return;
            }
            showLoader("Configuring routine...");
            const res = await apiFetch("/api/routines", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            hideLoader();
            if (res.ok) {
                document.getElementById("routineTitle").value = "";
                showToast("Daily routine configured successfully!");
                loadDashboardData();
            } else {
                const err = await res.json();
                showToast(err.error || "Failed to add routine", "error");
            }
        };

        async function loadDashboardData() {
            const res = await apiFetch("/api/data");
            if (!res.ok) return;
            const data = await res.json();

            cachedTasks = data.tasks.filter(t => currentUser.role === "CEO" || t.assigned_to === currentUser.name);
            renderTasks(cachedTasks);

            const routinesContainer = document.getElementById("dailyRoutinesList");
            const configRoutinesContainer = document.getElementById("configRoutinesList");

            if (configRoutinesContainer) {
                configRoutinesContainer.innerHTML = data.routines.length === 0
                    ? "<span style='color:var(--subtext);'>No daily routines configured.</span>"
                    : data.routines.map(r => `<div style="padding:10px 0; border-bottom:1px solid var(--border-color);"><b>${escapeHtml(r.title)}</b> - Assigned to: ${escapeHtml(r.assigned_to)}</div>`).join("");
            }

            const viewableRoutines = data.routines.filter(r => currentUser.role === "CEO" || r.assigned_to === currentUser.name);
            if (viewableRoutines.length === 0) {
                routinesContainer.innerHTML = "<span style='color:var(--subtext);'>No daily routines for today.</span>";
            } else {
                routinesContainer.innerHTML = viewableRoutines.map(r => {
                    const isDoneToday = r.today_status === "Completed";
                    const statusBadge = isDoneToday
                        ? '<span class="badge-done"><span class="pulsing-dot"></span>Done Today</span>'
                        : '<span class="badge-pending"><span class="pulsing-dot"></span>Pending Today</span>';
                    const actionHtml = (currentUser.role !== "CEO" && !isDoneToday)
                        ? `<div style="margin-top:12px;"><button type="button" onclick="window.markRoutineDone(${r.id})" style="background:#059669; width:auto; padding:8px 16px; font-size:0.85em;">Mark Completed for Today</button></div>`
                        : "";

                    return `
                        <div style="background:var(--input-bg); padding:20px; margin:14px 0; border-radius:12px; border:1px solid var(--border-color);">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <div>
                                    <strong style="font-size:1.05em;">${escapeHtml(r.title)}</strong>
                                    <div style="font-size:0.85em; color:var(--subtext); margin-top:4px;">Assigned to: ${escapeHtml(r.assigned_to)} - Resets daily</div>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                            ${actionHtml}
                        </div>
                    `;
                }).join("");
            }

            const historyBody = document.getElementById("historyTableBody");
            historyBody.innerHTML = data.history.length === 0
                ? `<tr><td colspan="2" style="color:var(--subtext);">No history recorded yet.</td></tr>`
                : data.history.map(h => `<tr><td style="color:var(--subtext); width:150px;">${escapeHtml(h.time)}</td><td>${escapeHtml(h.action)}</td></tr>`).join("");
        }

        function renderTasks(tasks) {
            const searchTerm = document.getElementById("filterSearch").value.toLowerCase();
            const statusFilter = document.getElementById("filterStatus").value;
            const priorityFilter = document.getElementById("filterPriority").value;
            const sortBy = document.getElementById("sortBy").value;

            const filtered = tasks.filter(t => {
                const matchesSearch = t.title.toLowerCase().includes(searchTerm) || t.assigned_to.toLowerCase().includes(searchTerm);
                const matchesStatus = !statusFilter || t.status === statusFilter;
                const matchesPriority = !priorityFilter || t.priority === priorityFilter;
                return matchesSearch && matchesStatus && matchesPriority;
            });

            filtered.sort((a, b) => {
                if (sortBy === "priority") {
                    const rank = { High: 3, Medium: 2, Low: 1 };
                    return (rank[b.priority] || 2) - (rank[a.priority] || 2);
                } else if (sortBy === "deadline") {
                    return (a.deadline || "").localeCompare(b.deadline || "");
                } else {
                    return b.id - a.id;
                }
            });

            if (currentTaskView === "kanban") {
                renderKanbanView(filtered);
            } else {
                renderListView(filtered);
            }
        }

        function renderListView(tasks) {
            const container = document.getElementById("myTasksList");
            if (tasks.length === 0) {
                container.innerHTML = "<span style='color:var(--subtext);'>No tasks match your filters.</span>";
                return;
            }
            container.innerHTML = tasks.map(t => {
                const statusBadge = t.status === "Completed"
                    ? '<span class="badge-done"><span class="pulsing-dot"></span>Completed</span>'
                    : t.status === "Failed"
                        ? '<span class="badge-failed"><span class="pulsing-dot"></span>Failed</span>'
                        : '<span class="badge-pending"><span class="pulsing-dot"></span>Pending Proof</span>';

                let actionHtml = "";
                if (currentUser.role !== "CEO") {
                    actionHtml = `
                        <div style="margin-top: 16px; border-top: 1px solid var(--border-color); padding-top: 16px;">
                            <label style="font-size:0.85em; display:block; margin-bottom:8px; color:var(--subtext);">Upload Proof Image for AI Verification:</label>
                            <input type="file" id="proofFile_${t.id}" accept="image/*" style="margin-bottom:10px; padding:8px;" onchange="window.handleFileSelect(${t.id}, this)">
                            <button type="button" onclick="window.submitProof(${t.id})" style="background:#059669; padding:10px 16px; font-size:0.9em; width:auto;">Submit Proof</button>
                        </div>
                    `;
                }

                return `
                    <div style="background:var(--input-bg); padding:20px; margin:14px 0; border-radius:12px; border:1px solid var(--border-color);">
                        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                            <div>
                                <strong style="font-size:1.1em;">${escapeHtml(t.title)}</strong>
                                <div style="font-size:0.85em; color:var(--subtext); margin-top:6px;">
                                    Assigned to: ${escapeHtml(t.assigned_to)} - Priority: ${escapeHtml(t.priority)} - Deadline: ${escapeHtml(t.deadline || "None")}
                                </div>
                            </div>
                            <div>${statusBadge}</div>
                        </div>
                        ${t.ai_comment ? `<div style="margin-top:12px; font-size:0.85em; color:var(--subtext); background:var(--card-bg); padding:12px; border-radius:10px; border: 1px solid var(--border-color);"><strong>AI Feedback:</strong> ${escapeHtml(t.ai_comment)}</div>` : ""}
                        ${actionHtml}
                    </div>
                `;
            }).join("");
        }

        function renderKanbanView(tasks) {
            const pendingTasks = tasks.filter(t => t.status === "Pending");
            const completedTasks = tasks.filter(t => t.status === "Completed");
            const failedTasks = tasks.filter(t => t.status === "Failed");

            document.getElementById("countPending").innerText = pendingTasks.length;
            document.getElementById("countCompleted").innerText = completedTasks.length;
            document.getElementById("countFailed").innerText = failedTasks.length;

            document.getElementById("kanbanPending").innerHTML = pendingTasks.length === 0
                ? '<div style="color:var(--subtext); font-size:0.85em; text-align:center; padding:20px;">No tasks</div>'
                : pendingTasks.map(t => kanbanCardHtml(t)).join("");
            document.getElementById("kanbanCompleted").innerHTML = completedTasks.length === 0
                ? '<div style="color:var(--subtext); font-size:0.85em; text-align:center; padding:20px;">No tasks</div>'
                : completedTasks.map(t => kanbanCardHtml(t)).join("");
            document.getElementById("kanbanFailed").innerHTML = failedTasks.length === 0
                ? '<div style="color:var(--subtext); font-size:0.85em; text-align:center; padding:20px;">No tasks</div>'
                : failedTasks.map(t => kanbanCardHtml(t)).join("");
        }

        function kanbanCardHtml(t) {
            const canDrag = currentUser.role === "CEO" || (currentUser.permissions && currentUser.permissions.includes("assign_tasks"));
            return `
                <div class="kanban-card" ${canDrag ? `draggable="true" ondragstart="window.dragTaskStart(event, ${t.id})"` : ""}>
                    <div style="font-weight:600; font-size:0.95em; margin-bottom:6px;">${escapeHtml(t.title)}</div>
                    <div style="font-size:0.75em; color:var(--subtext); margin-bottom:8px;">Assigned: ${escapeHtml(t.assigned_to)} - ${escapeHtml(t.priority)}</div>
                    ${(!canDrag && t.status === "Pending") ? `
                        <div style="margin-top:8px; border-top:1px solid var(--border-color); padding-top:8px;">
                            <input type="file" id="proofFile_${t.id}" accept="image/*" style="font-size:0.75em; margin-bottom:6px; padding:4px;" onchange="window.handleFileSelect(${t.id}, this)">
                            <button type="button" onclick="window.submitProof(${t.id})" style="background:#059669; padding:6px 12px; font-size:0.8em; width:100%;">Submit Proof</button>
                        </div>
                    ` : ""}
                </div>
            `;
        }

        window.submitProof = async function(taskId) {
            let file = selectedProofFiles[taskId];
            if (!file) {
                const fileInput = document.getElementById(`proofFile_${taskId}`);
                if (fileInput && fileInput.files && fileInput.files[0]) file = fileInput.files[0];
            }
            if (!file) {
                showToast("Please select an image file first.", "error");
                return;
            }

            const reader = new FileReader();
            reader.onload = async function() {
                const base64Image = reader.result.split(",")[1];
                showLoader("Analyzing proof with Gemini AI...");
                try {
                    const res = await apiFetch("/api/tasks/verify-proof", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ task_id: taskId, image: base64Image, mime_type: file.type })
                    });
                    hideLoader();
                    const result = await res.json();
                    if (res.ok) {
                        showToast("AI Verification Complete: " + result.status);
                        loadDashboardData();
                    } else {
                        showToast("Error: " + (result.error || "Verification failed"), "error");
                    }
                } catch (err) {
                    hideLoader();
                    showToast("Network error while submitting proof.", "error");
                }
            };
            reader.readAsDataURL(file);
        };

        window.markRoutineDone = async function(routineId) {
            showLoader("Updating routine status...");
            const res = await apiFetch("/api/routines/complete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ routine_id: routineId })
            });
            hideLoader();
            if (res.ok) {
                showToast("Daily routine marked completed!");
                loadDashboardData();
            } else {
                const err = await res.json();
                showToast(err.error || "Failed to update routine.", "error");
            }
        };

        async function loadWorkspaceChat() {
            if (!currentUser || !authToken) return;
            const res = await apiFetch("/api/chat");
            if (!res.ok) return;
            const data = await res.json();
            const box = document.getElementById("chatBox");
            box.innerHTML = data.messages.length === 0
                ? '<i style="color:var(--subtext);">No messages yet. Say hi!</i>'
                : data.messages.map(m => `<div class="chat-message"><strong style="color:var(--accent);">${escapeHtml(m.sender)}:</strong> ${escapeHtml(m.text)} <span style="font-size:0.75em; color:var(--subtext); float:right;">${escapeHtml(m.time)}</span></div>`).join("");
            box.scrollTop = box.scrollHeight;
        }

        window.sendChatMessage = async function() {
            const input = document.getElementById("chatInput");
            const text = input.value.trim();
            if (!text) return;
            await apiFetch("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            });
            input.value = "";
            loadWorkspaceChat();
        };
    </script>
</body>
</html>
"""


# --- Public Pages ---
@app.route("/")
def landing_page():
    return render_template("landing.html")


@app.route("/app")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        google_client_id=GOOGLE_CLIENT_ID,
        apple_client_id=APPLE_CLIENT_ID
    )


@app.route("/favicon.ico")
def favicon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (11, 15, 23, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(15, 21, 35, 255), outline=(94, 106, 210, 255), width=2)
    draw.line([(18, 34), (28, 44), (46, 22)], fill=(94, 106, 210, 255), width=5, joint="curve")
    io_buf = io.BytesIO()
    img.save(io_buf, format="PNG")
    io_buf.seek(0)
    return send_file(io_buf, mimetype="image/png")


# --- OAuth Endpoints (Strict Token Verification) ---
@app.route("/api/auth/google", methods=["POST"])
def auth_google():
    token = (request.json or {}).get("credential")
    if not token or not GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google OAuth token or Client ID missing."}), 400
    try:
        idinfo = google_id_token.verify_oauth2_token(token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo["email"]
        name = idinfo.get("name", email.split("@")[0])
        oauth_token = jwt.encode(
            {"email": email, "name": name, "provider": "Google", "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)},
            SECRET_KEY, algorithm="HS256"
        )
        return jsonify({"success": True, "provider": "Google", "oauth_token": oauth_token})
    except Exception as e:
        return jsonify({"error": f"Invalid Google token: {str(e)}"}), 401


@app.route("/api/auth/apple", methods=["POST"])
def auth_apple():
    id_token = (request.json or {}).get("id_token")
    if not id_token:
        return jsonify({"error": "Apple ID token missing."}), 400
    try:
        apple_keys = requests.get("https://appleid.apple.com/auth/keys", timeout=5).json()
        header = jwt.get_unverified_header(id_token)
        apple_key = next((key for key in apple_keys["keys"] if key["kid"] == header["kid"]), None)
        if not apple_key:
            return jsonify({"error": "Invalid Apple public key ID."}), 400
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(apple_key))
        payload = jwt.decode(id_token, public_key, audience=APPLE_CLIENT_ID, algorithms=["RS256"])
        email = payload.get("email")
        name = payload.get("name", email.split("@")[0] if email else "Apple User")
        oauth_token = jwt.encode(
            {"email": email, "name": name, "provider": "Apple", "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)},
            SECRET_KEY, algorithm="HS256"
        )
        return jsonify({"success": True, "provider": "Apple", "oauth_token": oauth_token})
    except Exception as e:
        return jsonify({"error": f"Invalid Apple token: {str(e)}"}), 401


# --- Standard & Social Auth ---
@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role", "Employee")

    if not name or not email or not password:
        return jsonify({"error": "All fields (name, email, password) are required."}), 400

    with get_db() as conn:
        existing = conn.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already registered."}), 400

        pw_hash = generate_password_hash(password)
        status = "Approved" if role == "CEO" else "Pending"
        stance = "CEO" if role == "CEO" else ""
        perms = json.dumps(["handle_approvals", "kick_employees", "config_routines", "modify_stances", "assign_tasks", "view_logs"] if role == "CEO" else [])

        if role == "CEO":
            company_name = (data.get("company_name") or "").strip()
            if not company_name:
                return jsonify({"error": "Company name cannot be blank when creating a workspace."}), 400
            company_id = str(random.randint(1000000000, 9999999999))
            conn.execute(
                "INSERT INTO users (email, name, password_hash, role, status, stance, permissions, company_id, company_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (email, name, pw_hash, "CEO", status, stance, perms, company_id, company_name)
            )
            log_history(conn, company_id, f"Company '{company_name}' created by CEO {name}")
        else:
            company_id = (data.get("company_id") or "").strip()
            if not company_id:
                return jsonify({"error": "Workspace Company ID is required."}), 400
            ceo_row = conn.execute("SELECT company_name FROM users WHERE company_id = ? AND role = 'CEO'", (company_id,)).fetchone()
            if not ceo_row:
                return jsonify({"error": "Invalid Workspace ID. Company does not exist."}), 404
            company_name = ceo_row["company_name"]
            conn.execute(
                "INSERT INTO users (email, name, password_hash, role, status, stance, permissions, company_id, company_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (email, name, pw_hash, "Employee", status, stance, perms, company_id, company_name)
            )

        user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        user_dict = serialize_user(user_row)
        return jsonify({"success": True, "user": user_dict, "token": create_token(user_dict)})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    company_id = (data.get("company_id") or "").strip()

    if not email or not password or not company_id:
        return jsonify({"error": "Email, password, and Workspace ID are required."}), 400

    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], password):
            return jsonify({"error": "Invalid email or password."}), 401
        if str(row["company_id"]) != str(company_id):
            return jsonify({"error": "Invalid Workspace ID for this account."}), 401

        user_dict = serialize_user(row)
        return jsonify({"success": True, "user": user_dict, "token": create_token(user_dict)})


@app.route("/api/social-login", methods=["POST"])
def social_login():
    data = request.json or {}
    oauth_token = data.get("oauth_token")
    company_id = (data.get("company_id") or "").strip()

    if not oauth_token or not company_id:
        return jsonify({"error": "OAuth token and Workspace ID are required."}), 400

    try:
        verified = jwt.decode(oauth_token, SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return jsonify({"error": "OAuth verification expired or invalid."}), 401

    email = verified["email"].strip().lower()
    name = verified["name"]

    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            ceo_row = conn.execute("SELECT company_name FROM users WHERE company_id = ? AND role = 'CEO'", (company_id,)).fetchone()
            if not ceo_row:
                return jsonify({"error": "Invalid Workspace ID. Company does not exist."}), 404
            conn.execute(
                "INSERT INTO users (email, name, password_hash, role, status, stance, permissions, company_id, company_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (email, name, "", "Employee", "Pending", "", "[]", company_id, ceo_row["company_name"])
            )
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if str(row["company_id"]) != str(company_id):
            return jsonify({"error": "Invalid Workspace ID for this account."}), 401

        user_dict = serialize_user(row)
        return jsonify({"success": True, "user": user_dict, "token": create_token(user_dict)})


# --- Protected Workspace Endpoints ---
@app.route("/api/check-status", methods=["GET"])
@require_auth
def check_status():
    user = request.current_user
    if user["status"] == "Kicked":
        return jsonify({"status": "Kicked"})
    if user["status"] == "Approved":
        return jsonify({
            "status": "Approved",
            "company_name": user["company_name"],
            "stance": user["stance"],
            "permissions": user["permissions"]
        })
    return jsonify({"status": "Pending"})


@app.route("/api/data", methods=["GET"])
@require_auth
def get_data():
    user = request.current_user
    if user["status"] != "Approved":
        return jsonify({"error": "Account pending CEO approval."}), 403

    company_id = str(user["company_id"])
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE company_id = ? ORDER BY id DESC", (company_id,)).fetchall()]
        routines_rows = conn.execute("SELECT * FROM routines WHERE company_id = ?", (company_id,)).fetchall()
        logs_rows = conn.execute("SELECT routine_id, status FROM daily_logs WHERE log_date = ?", (today_date,)).fetchall()
        logs_map = {r["routine_id"]: r["status"] for r in logs_rows}

        routines = []
        for r in routines_rows:
            rd = dict(r)
            rd["today_status"] = logs_map.get(r["id"], "Pending")
            routines.append(rd)

        history = [dict(r) for r in conn.execute("SELECT * FROM history WHERE company_id = ? ORDER BY id DESC LIMIT 100", (company_id,)).fetchall()]

    return jsonify({"tasks": tasks, "routines": routines, "history": history})


@app.route("/api/tasks", methods=["POST"])
@require_auth
def add_task():
    user = request.current_user
    if not has_permission(user, "assign_tasks"):
        return jsonify({"error": "Permission denied."}), 403

    data = request.json or {}
    title = (data.get("title") or "").strip()
    assigned_to = (data.get("assigned_to") or "").strip()
    deadline = data.get("deadline") or ""
    priority = data.get("priority", "Medium")
    company_id = str(user["company_id"])

    if not title or not assigned_to:
        return jsonify({"error": "Task title and assignee cannot be blank."}), 400

    with get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (title, assigned_to, deadline, priority, status, ai_comment, company_id) VALUES (?, ?, ?, ?, 'Pending', '', ?)",
            (title, assigned_to, deadline, priority, company_id)
        )
        log_history(conn, company_id, f"Task '{title}' assigned to {assigned_to} (Deadline: {deadline or 'None'})")

    return jsonify({"success": True})


@app.route("/api/tasks/update-status", methods=["POST"])
@require_auth
def update_task_status():
    user = request.current_user
    if not has_permission(user, "assign_tasks"):
        return jsonify({"error": "Permission denied. Only managers or CEOs can manually override task statuses."}), 403

    data = request.json or {}
    task_id = data.get("task_id")
    new_status = data.get("status")

    if new_status not in ["Pending", "Completed", "Failed"]:
        return jsonify({"error": "Invalid status."}), 400

    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ? AND company_id = ?", (task_id, str(user["company_id"]))).fetchone()
        if not task:
            return jsonify({"error": "Task not found."}), 404

        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
        log_history(conn, user["company_id"], f"Task '{task['title']}' status manually overridden to {new_status} by {user['name']}")
    return jsonify({"success": True})


@app.route("/api/routines", methods=["POST"])
@require_auth
def add_routine():
    user = request.current_user
    if not has_permission(user, "config_routines"):
        return jsonify({"error": "Permission denied."}), 403

    data = request.json or {}
    title = (data.get("title") or "").strip()
    assigned_to = (data.get("assigned_to") or "").strip()
    company_id = str(user["company_id"])

    if not title or not assigned_to:
        return jsonify({"error": "Routine title and assignee cannot be blank."}), 400

    with get_db() as conn:
        conn.execute("INSERT INTO routines (title, assigned_to, company_id) VALUES (?, ?, ?)", (title, assigned_to, company_id))
        log_history(conn, company_id, f"Auto-daily routine '{title}' set for {assigned_to}")

    return jsonify({"success": True})


@app.route("/api/routines/complete", methods=["POST"])
@require_auth
def complete_routine():
    user = request.current_user
    routine_id = (request.json or {}).get("routine_id")
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    with get_db() as conn:
        routine = conn.execute("SELECT * FROM routines WHERE id = ? AND company_id = ?", (routine_id, str(user["company_id"]))).fetchone()
        if not routine:
            return jsonify({"error": "Routine not found."}), 404

        if user["role"] != "CEO" and routine["assigned_to"] != user["name"] and not has_permission(user, "assign_tasks"):
            return jsonify({"error": "Permission denied. You can only complete your own daily routines."}), 403

        conn.execute(
            "INSERT OR REPLACE INTO daily_logs (log_date, routine_id, status) VALUES (?, ?, 'Completed')",
            (today_date, routine_id)
        )
        log_history(conn, user["company_id"], f"Daily routine '{routine['title']}' marked completed for today by {user['name']}")

    return jsonify({"success": True})


@app.route("/api/tasks/verify-proof", methods=["POST"])
@require_auth
def verify_proof():
    user = request.current_user
    data = request.json or {}
    task_id = data.get("task_id")
    image_b64 = data.get("image")
    mime_type = data.get("mime_type", "image/jpeg")

    with get_db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ? AND company_id = ?", (task_id, str(user["company_id"]))).fetchone()
        if not task:
            return jsonify({"error": "Task not found."}), 404

        if not client:
            conn.execute("UPDATE tasks SET status = 'Completed', ai_comment = 'Simulated verification (API key missing).' WHERE id = ?", (task_id,))
            return jsonify({"success": True, "status": "Completed"})

        try:
            image_bytes = base64.b64decode(image_b64)
            prompt = (
                f"Evaluate if the uploaded image proves task completion for: '{task['title']}'."
                " Analyze the image accurately and return a structured assessment."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TaskProofEvaluation,
                ),
            )

            evaluation = json.loads(response.text)
            passed = evaluation.get("passed", False)
            reason = evaluation.get("reason", "No evaluation reason provided.")

            status = "Completed" if passed else "Failed"
            ai_comment = f"{'Passed' if passed else 'Failed'}: {reason}"

            conn.execute("UPDATE tasks SET status = ?, ai_comment = ? WHERE id = ?", (status, ai_comment, task_id))
            log_history(conn, user["company_id"], f"AI verified task '{task['title']}' for {task['assigned_to']}: {status}")
            return jsonify({"success": True, "status": status, "comment": ai_comment})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["GET", "POST"])
@require_auth
def workspace_chat():
    user = request.current_user
    if user["status"] != "Approved":
        return jsonify({"error": "Account pending approval."}), 403

    company_id = str(user["company_id"])
    with get_db() as conn:
        if request.method == "POST":
            text = ((request.json or {}).get("text") or "").strip()
            if not text:
                return jsonify({"error": "Message text cannot be blank."}), 400
            conn.execute(
                "INSERT INTO messages (company_id, sender, text, time) VALUES (?, ?, ?, ?)",
                (company_id, user["name"], text, datetime.datetime.now().strftime("%H:%M"))
            )
            return jsonify({"success": True})
        else:
            msgs = [dict(r) for r in conn.execute("SELECT sender, text, time FROM messages WHERE company_id = ? ORDER BY id ASC LIMIT 200", (company_id,)).fetchall()]
            return jsonify({"messages": msgs})


# --- Protected Admin Endpoints ---
@app.route("/api/admin/pending-users", methods=["GET"])
@require_auth
def get_pending_users():
    user = request.current_user
    if not has_permission(user, "handle_approvals"):
        return jsonify({"error": "Permission denied."}), 403

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users WHERE company_id = ? AND status = 'Pending'", (str(user["company_id"]),)).fetchall()
        return jsonify({"pending_users": [serialize_user(r) for r in rows]})


@app.route("/api/admin/approve-user", methods=["POST"])
@require_auth
def approve_user():
    admin = request.current_user
    if not has_permission(admin, "handle_approvals"):
        return jsonify({"error": "Permission denied."}), 403

    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    stance = (data.get("stance") or "").strip() or "Team Member"
    permissions = data.get("permissions", [])

    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE email = ? AND company_id = ? AND status = 'Pending'", (email, str(admin["company_id"]))).fetchone()
        if not target:
            return jsonify({"error": "Pending user not found in your workspace."}), 404

        conn.execute(
            "UPDATE users SET status = 'Approved', stance = ?, permissions = ? WHERE email = ?",
            (stance, json.dumps(permissions), email)
        )
        log_history(conn, admin["company_id"], f"Employee {target['name']} approved with stance '{stance}' by {admin['name']}")
        return jsonify({"success": True})


@app.route("/api/admin/employees-list", methods=["GET"])
@require_auth
def get_employees_list():
    user = request.current_user
    if user["status"] != "Approved":
        return jsonify({"error": "Unauthorized."}), 403

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users WHERE company_id = ? AND status = 'Approved'", (str(user["company_id"]),)).fetchall()
        return jsonify({"employees": [serialize_user(r) for r in rows]})


@app.route("/api/admin/kick-employee", methods=["POST"])
@require_auth
def kick_employee():
    admin = request.current_user
    if not has_permission(admin, "kick_employees"):
        return jsonify({"error": "Permission denied."}), 403

    email = ((request.json or {}).get("email") or "").strip().lower()
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE email = ? AND company_id = ?", (email, str(admin["company_id"]))).fetchone()
        if not target:
            return jsonify({"error": "Employee not found in your workspace."}), 404
        if target["role"] == "CEO":
            return jsonify({"error": "Cannot kick the workspace CEO."}), 400

        # Flag as Kicked instead of deleting right away so polling sees 'Kicked' and shows the toast
        conn.execute("UPDATE users SET status = 'Kicked' WHERE email = ?", (email,))
        log_history(conn, admin["company_id"], f"Employee {target['name']} was kicked from the workspace by {admin['name']}")
        return jsonify({"success": True})


@app.route("/api/admin/update-company", methods=["POST"])
@require_auth
def update_company():
    admin = request.current_user
    if admin["role"] != "CEO":
        return jsonify({"error": "Only the CEO can rename the workspace."}), 403

    new_name = ((request.json or {}).get("company_name") or "").strip()
    if not new_name:
        return jsonify({"error": "Company name cannot be blank."}), 400

    with get_db() as conn:
        conn.execute("UPDATE users SET company_name = ? WHERE company_id = ?", (new_name, str(admin["company_id"])))
        log_history(conn, admin["company_id"], f"Workspace company name updated to '{new_name}'")
        return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)