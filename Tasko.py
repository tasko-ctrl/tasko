import datetime
import os
import base64
from flask import Flask, jsonify, request, render_template_string
from google import genai
from google.genai import types

app = Flask(__name__)

# Initialize Gemini client if API key is available
client = genai.Client() if os.environ.get("GEMINI_API_KEY") else None

# In-memory databases
USERS_DB = {}
PENDING_USERS_DB = {}
TASKS_DB = []
HISTORY_DB = []
MESSAGES_DB = []

# Single-file HTML/JS Frontend template with Theme Toggle, Time-Bound Tasks, & Live Chat
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko - Workspace Manager</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --border-color: #475569;
            --input-bg: #334155;
            --subtext: #94a3b8;
        }
        .light-theme {
            --bg-color: #f1f5f9;
            --card-bg: #ffffff;
            --text-color: #0f172a;
            --border-color: #cbd5e1;
            --input-bg: #f8fafc;
            --subtext: #64748b;
        }
        body { font-family: Arial, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; padding: 20px; transition: background 0.3s, color 0.3s; }
        .card { background: var(--card-bg); padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 1px solid var(--border-color); }
        input, select, textarea, button { padding: 10px; margin: 5px 0; border-radius: 4px; border: 1px solid var(--border-color); background: var(--input-bg); color: var(--text-color); width: 100%; box-sizing: border-box; }
        button { background: #3b82f6; cursor: pointer; font-weight: bold; border: none; color: white; }
        button:hover { background: #2563eb; }
        .hidden { display: none !important; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; border-bottom: 1px solid var(--border-color); text-align: left; }
        th { background: var(--card-bg); }
        .badge-done { background: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em; }
        .badge-pending { background: #f59e0b; color: black; padding: 3px 8px; border-radius: 4px; font-size: 0.85em; }
        .badge-failed { background: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em; }
        .chat-box { height: 200px; overflow-y: scroll; border: 1px solid var(--border-color); background: var(--input-bg); padding: 10px; border-radius: 4px; margin-bottom: 10px; }
        .chat-message { margin-bottom: 8px; font-size: 0.9em; }
    </style>
</head>
<body>
    <div style="position: absolute; top: 20px; right: 20px;">
        <button onclick="toggleTheme()" style="width: auto; padding: 6px 12px; font-size: 0.85em; background: #64748b;">🌓 Theme</button>
    </div>

    <div id="app">
        <!-- AUTH CONTAINER -->
        <div id="authContainer" class="card" style="max-width: 400px; margin: 50px auto;">
            <h2>Tasko Portal</h2>
            <div id="loginForm">
                <h3>Login</h3>
                <input type="email" id="loginEmail" placeholder="Email">
                <input type="password" id="loginPassword" placeholder="Password">
                <button onclick="login()">Login</button>
                <p>Don't have an account? <a href="#" onclick="toggleAuth(true)" style="color:#60a5fa;">Sign up</a></p>
            </div>
            <div id="signupForm" class="hidden">
                <h3>Sign Up</h3>
                <input type="text" id="suName" placeholder="Full Name">
                <input type="email" id="suEmail" placeholder="Email">
                <input type="password" id="suPassword" placeholder="Password">
                <select id="suRole" onchange="toggleCompanyInput()">
                    <option value="Employee">Employee</option>
                    <option value="CEO">CEO (Create Workspace)</option>
                </select>
                <input type="text" id="suCompanyName" placeholder="Company Name" class="hidden">
                <input type="text" id="suCompanyId" placeholder="Workspace Company ID">
                <button onclick="signup()">Register</button>
                <p>Already have an account? <a href="#" onclick="toggleAuth(false)" style="color:#60a5fa;">Login</a></p>
            </div>
        </div>

        <!-- DASHBOARD CONTAINER -->
        <div id="dashboardContainer" class="hidden">
            <div style="display: flex; justify-content: space-between; align-items: center;" class="card">
                <h2>Welcome, <span id="userNameDisp"></span> (<span id="userRoleDisp"></span>)</h2>
                <button onclick="logout()" style="width: auto; background: #ef4444;">Logout</button>
            </div>

            <!-- CEO PANEL -->
            <div id="ceoPanel" class="card hidden">
                <h3>CEO Control Panel</h3>
                <p><strong>Workspace Company ID:</strong> <span id="dispCompanyId"></span></p>
                
                <h4>Pending Employee Approvals</h4>
                <div id="pendingUsersList">No pending users.</div>

                <h4 style="margin-top: 20px;">Workspace Employees</h4>
                <div id="employeesManageList">No employees in workspace.</div>
                
                <h4 style="margin-top: 20px;">Assign Daily Routine / Timed Task</h4>
                <input type="text" id="taskTitle" placeholder="Task Title & Instructions (e.g. Open Shop)">
                <select id="taskAssigneeSelect">
                    <option value="">Select Employee</option>
                </select>
                <label style="font-size: 0.9em; color: var(--subtext); display: block; margin-top: 5px;">Strict Time Limit / Deadline:</label>
                <input type="datetime-local" id="taskDeadline">
                <select id="taskPriority" style="margin-top: 10px;">
                    <option value="Low">Low Priority</option>
                    <option value="Medium" selected>Medium Priority</option>
                    <option value="High">High Priority</option>
                </select>
                <button onclick="createTask()">Assign Task</button>
            </div>

            <!-- SHARED / EMPLOYEE WORKSPACE -->
            <div class="card">
                <h3>My Assigned Tasks & Routines (AI Proof Verification)</h3>
                <div id="myTasksList">No tasks assigned.</div>
            </div>

            <!-- LIVE WORKSPACE CHAT -->
            <div class="card">
                <h3>Live Workspace Chat</h3>
                <div id="chatBox" class="chat-box"></div>
                <input type="text" id="chatInput" placeholder="Type a message to the team..." onkeydown="if(event.key==='Enter') sendChatMessage()">
                <button onclick="sendChatMessage()" style="width: auto; margin-top: 5px;">Send Message</button>
            </div>

            <!-- WORK HISTORY LOG -->
            <div class="card">
                <h3>Workspace Activity Log</h3>
                <table>
                    <thead>
                        <tr><th>Time</th><th>Activity Action</th></tr>
                    </thead>
                    <tbody id="historyTableBody">
                        <tr><td colspan="2">No history recorded yet.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let currentUser = JSON.parse(localStorage.getItem('tasko_user')) || null;
        let currentTheme = localStorage.getItem('tasko_theme') || 'dark';

        window.onload = () => {
            if (currentTheme === 'light') {
                document.body.classList.add('light-theme');
            }
            if (currentUser) {
                checkLoginState();
                setInterval(loadWorkspaceChat, 3000); // Polling for live chat
            }
        };

        function toggleTheme() {
            document.body.classList.toggle('light-theme');
            currentTheme = document.body.classList.contains('light-theme') ? 'light' : 'dark';
            localStorage.setItem('tasko_theme', currentTheme);
        }

        function toggleAuth(isSignup) {
            document.getElementById('loginForm').classList.toggle('hidden', isSignup);
            document.getElementById('signupForm').classList.toggle('hidden', !isSignup);
        }

        function toggleCompanyInput() {
            let role = document.getElementById('suRole').value;
            let isCeo = role === 'CEO';
            document.getElementById('suCompanyName').classList.toggle('hidden', !isCeo);
            document.getElementById('suCompanyId').classList.toggle('hidden', isCeo);
        }

        async function signup() {
            let data = {
                name: document.getElementById('suName').value,
                email: document.getElementById('suEmail').value,
                password: document.getElementById('suPassword').value,
                role: document.getElementById('suRole').value,
                company_name: document.getElementById('suCompanyName').value,
                company_id: document.getElementById('suCompanyId').value
            };
            let res = await fetch('/api/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            let result = await res.json();
            if(res.ok) {
                alert("Registered successfully!");
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
                container.innerHTML = "No pending users.";
                return;
            }
            container.innerHTML = data.pending_users.map(u => `
                <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0;">
                    <span>${u.name} (${u.email})</span>
                    <button onclick="approveUser('${u.email}')" style="width:auto; padding:5px 10px;">Approve</button>
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
                container.innerHTML = "No active employees.";
                return;
            }
            container.innerHTML = data.employees.map(e => `
                <div style="display:flex; justify-content:space-between; align-items:center; padding:5px 0; border-bottom:1px solid var(--border-color);">
                    <span>${e.name} (${e.email}) - <b>${e.role}</b></span>
                    <button onclick="kickEmployee('${e.email}')" style="width:auto; padding:5px 10px; background:#ef4444;">Kick</button>
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
                tasksContainer.innerHTML = "No tasks found.";
            } else {
                tasksContainer.innerHTML = userTasks.map((t, idx) => {
                    let statusBadge = t.status === 'Completed' ? '<span class="badge-done">Completed (AI Verified)</span>' : 
                                      t.status === 'Failed' ? '<span class="badge-failed">Failed AI Check</span>' : 
                                      '<span class="badge-pending">Pending Proof</span>';
                    
                    let actionHtml = '';
                    if(currentUser.role !== 'CEO') {
                        actionHtml = `
                            <div style="margin-top: 10px; border-top: 1px dashed var(--border-color); padding-top: 10px;">
                                <label style="font-size:0.85em; display:block; margin-bottom:4px;">Upload Image Proof for AI Verification:</label>
                                <input type="file" id="proofFile_${idx}" accept="image/*" style="margin-bottom:5px;">
                                <button onclick="submitProof(${data.tasks.indexOf(t)})" style="background:#10b981; padding:6px 12px; font-size:0.9em;">Submit Proof</button>
                            </div>
                        `;
                    }

                    return `
                        <div style="background:var(--input-bg); padding:12px; margin:8px 0; border-radius:6px; border:1px solid var(--border-color);">
                            <div>
                                <strong>${t.title}</strong> [Priority: ${t.priority}]<br>
                                <small>Assigned to: ${t.assigned_to} | ⏰ Deadline: ${t.deadline || 'None'}</small>
                                <br><small>Status: ${statusBadge}</small>
                                ${t.ai_comment ? `<br><small style="color:var(--subtext);"><strong>AI Feedback:</strong> ${t.ai_comment}</small>` : ''}
                            </div>
                            ${actionHtml}
                        </div>
                    `;
                }).join('');
            }

            let historyBody = document.getElementById('historyTableBody');
            let workspaceHistory = data.history.filter(h => !h.company_id || String(h.company_id) === String(currentUser.company_id));
            historyBody.innerHTML = workspaceHistory.length === 0 ? `<tr><td colspan="2">No history recorded yet.</td></tr>` : 
                workspaceHistory.map(h => `<tr><td>${h.time}</td><td>${h.action}</td></tr>`).join('');
        }

        async function submitProof(taskIndex) {
            let fileInput = document.getElementById(`proofFile_${taskIndex}`);
            if(fileInput.files.length === 0) { alert("Please select an image file."); return; }
            let file = fileInput.files[0];
            let reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onload = async function () {
                let base64Image = reader.result.split(',')[1];
                alert("Submitting proof to Gemini AI for verification...");
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
                data.messages.map(m => `<div class="chat-message"><strong>${m.sender}:</strong> ${m.text} <span style="font-size:0.75em; color:var(--subtext); float:right;">${m.time}</span></div>`).join('');
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


@app.route("/")
def index():
  return render_template_string(HTML_TEMPLATE)


@app.route("/api/signup", methods=["POST"])
def signup():
  data = request.json
  email = data.get("email")
  name = data.get("name")
  password = data.get("password")
  role = data.get("role", "Employee")

  if email in USERS_DB or email in PENDING_USERS_DB:
    return jsonify({"error": "Email already registered."}), 400

  user_data = {
      "name": name,
      "email": email,
      "password": password,
      "role": role,
      "status": "Jobless",
  }

  if role == "CEO":
    import random

    company_id = str(random.randint(1000000000, 9999999999))
    user_data["company_id"] = company_id
    user_data["company_name"] = data.get("company_name", "MyCorp")
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
    company_id = data.get("company_id")
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