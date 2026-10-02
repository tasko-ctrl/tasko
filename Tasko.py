import datetime
import os
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

# In-memory databases
USERS_DB = {}
PENDING_USERS_DB = {}
TASKS_DB = []
HISTORY_DB = []
MESSAGES_DB = []

# Single-file HTML/JS Frontend template with localStorage session persistence
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko - Workspace Manager</title>
    <style>
        body { font-family: Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
        .card { background: #1e293b; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        input, select, button { padding: 10px; margin: 5px 0; border-radius: 4px; border: 1px solid #475569; background: #334155; color: white; width: 100%; box-sizing: border-box; }
        button { background: #3b82f6; cursor: pointer; font-weight: bold; border: none; }
        button:hover { background: #2563eb; }
        .hidden { display: none !important; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 10px; border-bottom: 1px solid #334155; text-align: left; }
        th { background: #1e293b; }
    </style>
</head>
<body>
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
                
                <h4 style="margin-top: 20px;">Assign Task to Employee</h4>
                <input type="text" id="taskTitle" placeholder="Task Title">
                <select id="taskAssigneeSelect">
                    <option value="">Select Employee</option>
                </select>
                <select id="taskPriority">
                    <option value="Low">Low Priority</option>
                    <option value="Medium" selected>Medium Priority</option>
                    <option value="High">High Priority</option>
                </select>
                <button onclick="createTask()">Assign Task</button>
            </div>

            <!-- SHARED / EMPLOYEE WORKSPACE -->
            <div class="card">
                <h3>My Tasks</h3>
                <div id="myTasksList">No tasks assigned.</div>
            </div>

            <!-- WORK HISTORY LOG -->
            <div class="card">
                <h3>Workspace Work History & Activity Log</h3>
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

        window.onload = () => {
            if (currentUser) {
                checkLoginState();
            }
        };

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
                alert("Registered successfully! Logging you in.");
                currentUser = result.user || data; 
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
                loadEmployeesDropdown();
            } else {
                ceoPanel.classList.add('hidden');
            }
            loadDashboardData();
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
            loadEmployeesDropdown();
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
                priority: document.getElementById('taskPriority').value,
                company_id: currentUser.company_id
            };
            if(!data.title || !data.assigned_to) {
                alert("Please fill out all task fields.");
                return;
            }
            await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            document.getElementById('taskTitle').value = '';
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
                tasksContainer.innerHTML = userTasks.map((t, idx) => `
                    <div style="background:#334155; padding:10px; margin:5px 0; border-radius:4px; display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <strong>${t.title}</strong> [Priority: ${t.priority}] - Assigned to: ${t.assigned_to} 
                            <br><small>Status: <b>${t.status}</b></small>
                        </div>
                        <div>
                            <select onchange="updateTaskStatus(${data.tasks.indexOf(t)}, this.value)" style="width:auto;">
                                <option value="Pending" ${t.status==='Pending'?'selected':''}>Pending</option>
                                <option value="In Progress" ${t.status==='In Progress'?'selected':''}>In Progress</option>
                                <option value="Completed" ${t.status==='Completed'?'selected':''}>Completed</option>
                            </select>
                        </div>
                    </div>
                `).join('');
            }

            let historyBody = document.getElementById('historyTableBody');
            let workspaceHistory = data.history.filter(h => !h.company_id || String(h.company_id) === String(currentUser.company_id));
            if(workspaceHistory.length === 0) {
                historyBody.innerHTML = `<tr><td colspan="2">No history recorded yet.</td></tr>`;
            } else {
                historyBody.innerHTML = workspaceHistory.map(h => `
                    <tr><td>${h.time}</td><td>${h.action}</td></tr>
                `).join('');
            }
        }

        async function updateTaskStatus(index, status) {
            await fetch('/api/tasks/status', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index, status})});
            loadDashboardData();
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
    matching_ceo = next(
        (
            u
            for u in USERS_DB.values()
            if u.get("company_id") == company_id and u.get("role") == "CEO"
        ),
        None,
    )
    user_data["company_name"] = (
        matching_ceo.get("company_name") if matching_ceo else "Workspace"
    )
    PENDING_USERS_DB[email] = user_data

  return jsonify(
      {"success": True, "message": "Signup successful.", "user": user_data}
  )


@app.route("/api/login", methods=["POST"])
def login():
  data = request.json
  email = data.get("email")
  password = data.get("password")
  user = USERS_DB.get(email) or PENDING_USERS_DB.get(email)
  if not user or user.get("password") != password:
    return jsonify({"error": "Invalid email or password."}), 401
  return jsonify({"success": True, "user": user})


@app.route("/api/data", methods=["GET"])
def get_data():
  company_id = request.args.get("company_id")
  filtered_tasks = [
      t for t in TASKS_DB if str(t.get("company_id")) == str(company_id)
  ]
  filtered_history = [
      h
      for h in HISTORY_DB
      if str(h.get("company_id")) == str(company_id)
      or "company_id" not in h
  ]
  return jsonify({"tasks": filtered_tasks, "history": filtered_history})


@app.route("/api/tasks", methods=["POST"])
def add_task():
  data = request.json
  title = data.get("title")
  assigned_to = data.get("assigned_to")
  priority = data.get("priority", "Medium")
  company_id = data.get("company_id")

  TASKS_DB.append({
      "title": title,
      "assigned_to": assigned_to,
      "priority": priority,
      "status": "Pending",
      "company_id": company_id,
  })

  HISTORY_DB.insert(
      0,
      {
          "company_id": company_id,
          "action": f"Task '{title}' assigned to {assigned_to} ({priority} Priority)",
          "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
      },
  )
  return jsonify({"success": True})


@app.route("/api/tasks/status", methods=["POST"])
def update_task_status():
  data = request.json
  index = data.get("index")
  status = data.get("status")
  if 0 <= index < len(TASKS_DB):
    task = TASKS_DB[index]
    task["status"] = status
    HISTORY_DB.insert(
        0,
        {
            "company_id": task.get("company_id"),
            "action": f"Task '{task['title']}' status updated to {status}",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    return jsonify({"success": True})
  return jsonify({"error": "Task not found."}), 404


@app.route("/api/admin/pending-users", methods=["GET"])
def get_pending_users():
  company_id = request.args.get("company_id")
  pending = [
      u
      for u in PENDING_USERS_DB.values()
      if str(u.get("company_id")) == str(company_id)
  ]
  return jsonify({"pending_users": pending})


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
  employees = [
      {"name": u.get("name"), "email": u.get("email"), "role": u.get("role")}
      for u in USERS_DB.values()
      if str(u.get("company_id")) == str(company_id)
      and u.get("status") == "Approved"
  ]
  return jsonify({"employees": employees})


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)