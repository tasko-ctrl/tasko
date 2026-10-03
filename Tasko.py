import datetime
import os
import base64
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, jsonify, request, render_template_string
from google import genai
from google.genai import types

app = Flask(__name__)

client = genai.Client() if os.environ.get("GEMINI_API_KEY") else None

USERS_DB = {}
PENDING_USERS_DB = {}
OTP_DB = {}
TASKS_DB = []
HISTORY_DB = []
MESSAGES_DB = []

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko - Workspace Manager</title>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #121826;
            --text-color: #f3f4f6;
            --border-color: #1f293d;
            --input-bg: #0d121f;
            --subtext: #8a99ad;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
        }
        .light-theme {
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-color: #0f172a;
            --border-color: #e2e8f0;
            --input-bg: #f1f5f9;
            --subtext: #64748b;
            --accent: #4f46e5;
            --accent-hover: #4338ca;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; padding: 24px; transition: background 0.2s, color 0.2s; }
        .card { background: var(--card-bg); padding: 24px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 20px -2px rgba(0,0,0,0.3); border: 1px solid var(--border-color); }
        input, select, textarea { padding: 10px 14px; margin: 6px 0 14px 0; border-radius: 6px; border: 1px solid var(--border-color); background: var(--input-bg); color: var(--text-color); width: 100%; box-sizing: border-box; font-size: 0.95em; outline: none; transition: border-color 0.2s; }
        input:focus, select:focus, textarea:focus { border-color: var(--accent); }
        button { background: var(--accent); cursor: pointer; font-weight: 600; border: none; color: white; padding: 10px 16px; border-radius: 6px; width: 100%; font-size: 0.95em; transition: background 0.2s; }
        button:hover { background: var(--accent-hover); }
        .hidden { display: none !important; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
        th, td { padding: 12px; border-bottom: 1px solid var(--border-color); text-align: left; }
        th { background: var(--input-bg); color: var(--subtext); font-weight: 600; }
        .badge-done { background: #059669; color: white; padding: 4px 10px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .badge-pending { background: #d97706; color: white; padding: 4px 10px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .badge-failed { background: #dc2626; color: white; padding: 4px 10px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .chat-box { height: 220px; overflow-y: scroll; border: 1px solid var(--border-color); background: var(--input-bg); padding: 12px; border-radius: 8px; margin-bottom: 12px; }
        .chat-message { margin-bottom: 10px; font-size: 0.9em; line-height: 1.4; }
        h2, h3, h4 { margin-top: 0; font-weight: 600; letter-spacing: -0.01em; }
        a { color: var(--accent); text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div style="position: absolute; top: 24px; right: 24px;">
        <button onclick="toggleTheme()" style="width: auto; padding: 6px 12px; font-size: 0.85em; background: var(--input-bg); border: 1px solid var(--border-color); color: var(--text-color);">🌓 Theme</button>
    </div>

    <div id="app" style="max-width: 800px; margin: 0 auto;">
        <!-- AUTH CONTAINER -->
        <div id="authContainer" class="card" style="max-width: 420px; margin: 80px auto;">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:20px;">
                <div style="width:12px; height:12px; background:var(--accent); border-radius:50%;"></div>
                <h2 style="margin:0;">Tasko Workspace</h2>
            </div>
            
            <div id="loginForm">
                <h3 style="color:var(--subtext); font-size:1em; margin-bottom:16px;">Sign in to your workspace</h3>
                <input type="email" id="loginEmail" placeholder="Email address">
                <input type="password" id="loginPassword" placeholder="Password">
                <button onclick="login()">Continue</button>
                <p style="margin-top:16px; font-size:0.9em; text-align:center;">Don't have an account? <a href="#" onclick="toggleAuth('signup')">Sign up</a></p>
            </div>

            <div id="signupForm" class="hidden">
                <h3 style="color:var(--subtext); font-size:1em; margin-bottom:16px;">Create a new account</h3>
                <input type="text" id="suName" placeholder="Full Name">
                <input type="email" id="suEmail" placeholder="Email address">
                <input type="password" id="suPassword" placeholder="Password">
                <select id="suRole" onchange="toggleCompanyInput()">
                    <option value="Employee">Employee</option>
                    <option value="CEO">CEO (Create Workspace)</option>
                </select>
                <input type="text" id="suCompanyName" placeholder="Company Name" class="hidden">
                <input type="text" id="suCompanyId" placeholder="Workspace Company ID">
                <button onclick="registerAccount()">Send Verification Code</button>
                <p style="margin-top:16px; font-size:0.9em; text-align:center;">Already have an account? <a href="#" onclick="toggleAuth('login')">Sign in</a></p>
            </div>

            <div id="otpForm" class="hidden">
                <h3 style="color:var(--subtext); font-size:1em; margin-bottom:6px;">Verify your email</h3>
                <p style="font-size:0.85em; color:var(--subtext); margin-bottom:16px;">Enter the 6-digit verification code sent to your inbox.</p>
                <input type="text" id="otpCode" placeholder="000000" maxlength="6" style="text-align:center; font-size:1.4em; letter-spacing:6px;">
                <button onclick="verifyOtp()">Verify Code</button>
                <p style="margin-top:16px; font-size:0.9em; text-align:center;"><a href="#" onclick="toggleAuth('signup')">Back to Sign Up</a></p>
            </div>
        </div>

        <!-- DASHBOARD CONTAINER -->
        <div id="dashboardContainer" class="hidden">
            <div style="display: flex; justify-content: space-between; align-items: center;" class="card">
                <div>
                    <h2 style="margin:0; font-size:1.25em;"><span id="userNameDisp"></span></h2>
                    <span style="font-size:0.85em; color:var(--subtext);">Role: <span id="userRoleDisp"></span></span>
                </div>
                <button onclick="logout()" style="width: auto; background: #dc2626; padding: 8px 14px; font-size:0.85em;">Logout</button>
            </div>

            <!-- CEO PANEL -->
            <div id="ceoPanel" class="card hidden">
                <h3>CEO Control Panel</h3>
                <p style="font-size:0.9em; color:var(--subtext);">Workspace Company ID: <strong style="color:var(--text-color);" id="dispCompanyId"></strong></p>
                
                <h4 style="margin-top:20px; font-size:0.95em; color:var(--subtext);">Pending Employee Approvals</h4>
                <div id="pendingUsersList" style="font-size:0.9em;">No pending users.</div>

                <h4 style="margin-top: 24px; font-size:0.95em; color:var(--subtext);">Workspace Employees</h4>
                <div id="employeesManageList" style="font-size:0.9em;">No employees in workspace.</div>
                
                <h4 style="margin-top: 24px; font-size:0.95em; color:var(--subtext);">Assign Routine / Timed Task</h4>
                <input type="text" id="taskTitle" placeholder="Task Title & Instructions (e.g. Open Shop)">
                <select id="taskAssigneeSelect">
                    <option value="">Select Employee</option>
                </select>
                <label style="font-size: 0.85em; color: var(--subtext); display: block; margin-top: 10px;">Strict Deadline:</label>
                <input type="datetime-local" id="taskDeadline">
                <select id="taskPriority" style="margin-top: 6px;">
                    <option value="Low">Low Priority</option>
                    <option value="Medium" selected>Medium Priority</option>
                    <option value="High">High Priority</option>
                </select>
                <button onclick="createTask()" style="margin-top:10px;">Assign Task</button>
            </div>

            <!-- SHARED / EMPLOYEE WORKSPACE -->
            <div class="card">
                <h3>Assigned Tasks & Routines</h3>
                <div id="myTasksList">No tasks assigned.</div>
            </div>

            <!-- LIVE WORKSPACE CHAT -->
            <div class="card">
                <h3>Live Workspace Chat</h3>
                <div id="chatBox" class="chat-box"></div>
                <div style="display:flex; gap:10px;">
                    <input type="text" id="chatInput" placeholder="Type a message to the team..." onkeydown="if(event.key==='Enter') sendChatMessage()" style="margin:0;">
                    <button onclick="sendChatMessage()" style="width: auto; margin:0; padding:0 20px;">Send</button>
                </div>
            </div>

            <!-- WORK HISTORY LOG -->
            <div class="card">
                <h3>Workspace Activity Log</h3>
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
    </div>

    <script>
        let currentUser = JSON.parse(localStorage.getItem('tasko_user')) || null;
        let currentTheme = localStorage.getItem('tasko_theme') || 'dark';
        let tempSignupData = {};

        window.onload = () => {
            if (currentTheme === 'light') { document.body.classList.add('light-theme'); }
            if (currentUser) {
                checkLoginState();
                setInterval(loadWorkspaceChat, 3000);
            }
        };

        function toggleTheme() {
            document.body.classList.toggle('light-theme');
            currentTheme = document.body.classList.contains('light-theme') ? 'light' : 'dark';
            localStorage.setItem('tasko_theme', currentTheme);
        }

        function toggleAuth(formType) {
            document.getElementById('loginForm').classList.toggle('hidden', formType !== 'login');
            document.getElementById('signupForm').classList.toggle('hidden', formType !== 'signup');
            document.getElementById('otpForm').classList.toggle('hidden', formType !== 'otp');
        }

        function toggleCompanyInput() {
            let role = document.getElementById('suRole').value;
            let isCeo = role === 'CEO';
            document.getElementById('suCompanyName').classList.toggle('hidden', !isCeo);
            document.getElementById('suCompanyId').classList.toggle('hidden', isCeo);
        }

        async function registerAccount() {
            tempSignupData = {
                name: document.getElementById('suName').value,
                email: document.getElementById('suEmail').value,
                password: document.getElementById('suPassword').value,
                role: document.getElementById('suRole').value,
                company_name: document.getElementById('suCompanyName').value,
                company_id: document.getElementById('suCompanyId').value
            };
            if(!tempSignupData.name || !tempSignupData.email || !tempSignupData.password) {
                alert("Please fill out all fields.");
                return;
            }
            let res = await fetch('/api/signup-request', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(tempSignupData)});
            let result = await res.json();
            if(res.ok) {
                alert("Verification code sent to your email!");
                toggleAuth('otp');
            } else { alert(result.error); }
        }

        async function verifyOtp() {
            let otp = document.getElementById('otpCode').value;
            let payload = { email: tempSignupData.email, otp: otp, signup_data: tempSignupData };
            let res = await fetch('/api/verify-otp', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
            let result = await res.json();
            if(res.ok) {
                alert("Verified successfully!");
                currentUser = result.user;
                localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                checkLoginState();
            } else { alert(result.error); }
        }

        async function login() {
            let data = {
                email: document.getElementById('loginEmail').value,
                password: document.getElementById('loginPassword').value
            };
            let res = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            let result = await res.json();
            if(res.ok) {
                currentUser = result.user;
                localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                checkLoginState();
            } else { alert(result.error); }
        }

        function logout() {
            currentUser = null;
            localStorage.removeItem('tasko_user');
            document.getElementById('authContainer').classList.remove('hidden');
            document.getElementById('dashboardContainer').classList.add('hidden');
            toggleAuth('login');
        }

        async function checkLoginState() {
            if(!currentUser) return;
            document.getElementById('authContainer').classList.add('hidden');
            document.getElementById('dashboardContainer').classList.remove('hidden');
            document.getElementById('userNameDisp').innerText = currentUser.name;
            document.getElementById('userRoleDisp').innerText = currentUser.role;

            let isCeo = currentUser.role === 'CEO';
            let ceoPanel = document.getElementById('ceoPanel');
            
            if(isCeo) {
                ceoPanel.classList.remove('hidden');
                document.getElementById('dispCompanyId').innerText = currentUser.company_id;
                loadPendingUsers();
                loadEmployeesManagement();
                loadEmployeesDropdown();
            } else {
                ceoPanel.classList.add('hidden');
            }
            loadDashboardData();
            loadWorkspaceChat();
        }

        async function loadPendingUsers() {
            let res = await fetch(`/api/admin/pending-users?company_id=${currentUser.company_id}`);
            let data = await res.json();
            let container = document.getElementById('pendingUsersList');
            if(data.pending_users.length === 0) {
                container.innerHTML = "<span style='color:var(--subtext);'>No pending users.</span>";
                return;
            }
            container.innerHTML = data.pending_users.map(u => `
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid var(--border-color);">
                    <span>${u.name} (${u.email})</span>
                    <button onclick="approveUser('${u.email}')" style="width:auto; padding:4px 10px; font-size:0.85em;">Approve</button>
                </div>
            `).join('');
        }

        async function approveUser(email) {
            await fetch('/api/admin/approve-user', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email})});
            loadPendingUsers();
            loadEmployeesManagement();
            loadEmployeesDropdown();
        }

        async function loadEmployeesManagement() {
            let res = await fetch(`/api/admin/employees-list?company_id=${currentUser.company_id}`);
            let data = await res.json();
            let container = document.getElementById('employeesManageList');
            if(data.employees.length === 0) {
                container.innerHTML = "<span style='color:var(--subtext);'>No active employees.</span>";
                return;
            }
            container.innerHTML = data.employees.map(e => `
                <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid var(--border-color);">
                    <span>${e.name} (${e.email}) - <b style="color:var(--accent);">${e.role}</b></span>
                    <button onclick="kickEmployee('${e.email}')" style="width:auto; padding:4px 10px; font-size:0.85em; background:#dc2626;">Kick</button>
                </div>
            `).join('');
        }

        async function kickEmployee(email) {
            if(!confirm("Are you sure you want to kick this employee?")) return;
            await fetch('/api/admin/kick-employee', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email, company_id: currentUser.company_id})});
            loadEmployeesManagement();
            loadEmployeesDropdown();
            loadDashboardData();
        }

        async function loadEmployeesDropdown() {
            let res = await fetch(`/api/admin/employees-list?company_id=${currentUser.company_id}`);
            let data = await res.json();
            let select = document.getElementById('taskAssigneeSelect');
            select.innerHTML = '<option value="">Select Employee</option>' + data.employees.map(e => `<option value="${e.name}">${e.name} (${e.role})</option>`).join('');
        }

        async function createTask() {
            let data = {
                title: document.getElementById('taskTitle').value,
                assigned_to: document.getElementById('taskAssigneeSelect').value,
                deadline: document.getElementById('taskDeadline').value,
                priority: document.getElementById('taskPriority').value,
                company_id: currentUser.company_id
            };
            if(!data.title || !data.assigned_to) {
                alert("Please fill out task title and assignee.");
                return;
            }
            await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            document.getElementById('taskTitle').value = '';
            document.getElementById('taskDeadline').value = '';
            loadDashboardData();
        }

        async function loadDashboardData() {
            let res = await fetch(`/api/data?company_id=${currentUser.company_id}`);
            let data = await res.json();
            
            let tasksContainer = document.getElementById('myTasksList');
            let userTasks = data.tasks.filter(t => String(t.company_id) === String(currentUser.company_id) && (currentUser.role === 'CEO' || t.assigned_to === currentUser.name));
            
            if(userTasks.length === 0) {
                tasksContainer.innerHTML = "<span style='color:var(--subtext);'>No tasks found.</span>";
            } else {
                tasksContainer.innerHTML = userTasks.map((t, idx) => {
                    let statusBadge = t.status === 'Completed' ? '<span class="badge-done">Completed</span>' : 
                                      t.status === 'Failed' ? '<span class="badge-failed">Failed</span>' : 
                                      '<span class="badge-pending">Pending Proof</span>';
                    
                    let actionHtml = '';
                    if(currentUser.role !== 'CEO') {
                        actionHtml = `
                            <div style="margin-top: 12px; border-top: 1px solid var(--border-color); padding-top: 12px;">
                                <label style="font-size:0.85em; display:block; margin-bottom:6px; color:var(--subtext);">Upload Proof Image for AI Verification:</label>
                                <input type="file" id="proofFile_${idx}" accept="image/*" style="margin-bottom:8px; padding:6px;">
                                <button onclick="submitProof(${data.tasks.indexOf(t)})" style="background:#059669; padding:8px 14px; font-size:0.9em; width:auto;">Submit Proof</button>
                            </div>
                        `;
                    }

                    return `
                        <div style="background:var(--input-bg); padding:14px; margin:10px 0; border-radius:8px; border:1px solid var(--border-color);">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                                <div>
                                    <strong style="font-size:1.05em;">${t.title}</strong>
                                    <div style="font-size:0.85em; color:var(--subtext); margin-top:4px;">
                                        Assigned to: ${t.assigned_to} &bull; Priority: ${t.priority} &bull; Deadline: ${t.deadline || 'None'}
                                    </div>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                            ${t.ai_comment ? `<div style="margin-top:8px; font-size:0.85em; color:var(--subtext); background:var(--card-bg); padding:8px; border-radius:6px;"><strong>AI Feedback:</strong> ${t.ai_comment}</div>` : ''}
                            ${actionHtml}
                        </div>
                    `;
                }).join('');
            }

            let historyBody = document.getElementById('historyTableBody');
            let workspaceHistory = data.history.filter(h => !h.company_id || String(h.company_id) === String(currentUser.company_id));
            historyBody.innerHTML = workspaceHistory.length === 0 ? `<tr><td colspan="2" style="color:var(--subtext);">No history recorded yet.</td></tr>` : 
                workspaceHistory.map(h => `<tr><td style="color:var(--subtext); width:140px;">${h.time}</td><td>${h.action}</td></tr>`).join('');
        }

        async function submitProof(taskIndex) {
            let fileInput = document.getElementById(`proofFile_${taskIndex}`);
            if(fileInput.files.length === 0) { alert("Please select an image file."); return; }
            let file = fileInput.files[0];
            let reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onload = async function () {
                let base64Image = reader.result.split(',')[1];
                alert("Submitting proof to Gemini AI...");
                let res = await fetch('/api/tasks/verify-proof', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index: taskIndex, image: base64Image, mime_type: file.type})});
                let result = await res.json();
                if(res.ok) { alert("AI Verification Complete: " + result.status); loadDashboardData(); }
            };
        }

        async function loadWorkspaceChat() {
            if(!currentUser) return;
            let res = await fetch(`/api/chat?company_id=${currentUser.company_id}`);
            let data = await res.json();
            let box = document.getElementById('chatBox');
            box.innerHTML = data.messages.length === 0 ? '<i style="color:var(--subtext);">No messages yet. Say hi!</i>' :
                data.messages.map(m => `<div class="chat-message"><strong style="color:var(--accent);">${m.sender}:</strong> ${m.text} <span style="font-size:0.75em; color:var(--subtext); float:right;">${m.time}</span></div>`).join('');
            box.scrollTop = box.scrollHeight;
        }

        async function sendChatMessage() {
            let input = document.getElementById('chatInput');
            let text = input.value.trim();
            if(!text) return;
            await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({company_id: currentUser.company_id, sender: currentUser.name, text})});
            input.value = '';
            loadWorkspaceChat();
        }
    </script>
</body>
</html>
"""


def send_otp_email(receiver_email, otp_code):
  sender_email = os.environ.get("SMTP_EMAIL")
  sender_password = os.environ.get("SMTP_PASS")

  if not sender_email or not sender_password:
    print(f"\n[TASKO OTP FALLBACK] Code for {receiver_email}: {otp_code}\n")
    return

  try:
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = "Tasko - Verification Code"

    body = f"Your Tasko verification code is: {otp_code}\nWelcome aboard!"
    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender_email, sender_password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()
    print(f"OTP successfully sent to {receiver_email}")
  except Exception as e:
    print(f"Failed to send email: {e}")
    print(f"\n[TASKO OTP FALLBACK] Code for {receiver_email}: {otp_code}\n")


@app.route("/")
def index():
  return render_template_string(HTML_TEMPLATE)


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
            "action": f"Company '{user_data['company_name']}' created by CEO {name}",
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
  return jsonify({
      "tasks": [
          t for t in TASKS_DB if str(t.get("company_id")) == str(company_id)
      ],
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
              f"Task/Routine '{title}' assigned to {assigned_to} (Deadline:"
              f" {deadline or 'None'})"
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
          {"name": u.get("name"), "email": u.get("email"), "role": u.get("role")}
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
            "action": (
                f"Employee {user.get('name')} was kicked from the workspace"
            ),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    return jsonify({"success": True})
  return jsonify({"error": "Employee not found."}), 404


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)