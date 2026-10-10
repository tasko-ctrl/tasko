import datetime
import os
import base64
import random
import requests
from flask import Flask, jsonify, request, render_template_string
from google import genai
from google.api_core import exceptions
from google.genai import types

app = Flask(__name__)

# Correctly initialize the Google GenAI client using GEMINI_API_KEY
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY")) if os.environ.get("GEMINI_API_KEY") else None

USERS_DB = {}
PENDING_USERS_DB = {}
TASKS_DB = []
DAILY_ROUTINES_DB = []
DAILY_LOGS_DB = {}  # Format: { "YYYY-MM-DD": { routine_index: status } }
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
            --sidebar-bg: #0f1624;
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
            --sidebar-bg: #f1f5f9;
            --card-bg: #ffffff;
            --text-color: #0f172a;
            --border-color: #e2e8f0;
            --input-bg: #f8fafc;
            --subtext: #64748b;
            --accent: #4f46e5;
            --accent-hover: #4338ca;
        }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; display: flex; height: 100vh; overflow: hidden; transition: background 0.2s, color 0.2s; }
        
        .sidebar { width: 260px; min-width: 260px; background: var(--sidebar-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 20px; box-sizing: border-box; height: 100vh; }
        .sidebar-brand { font-size: 1.2em; font-weight: 700; display: flex; align-items: center; gap: 10px; margin-bottom: 30px; color: var(--text-color); }
        .sidebar-menu { display: flex; flex-direction: column; gap: 8px; flex: 1; overflow-y: auto; }
        .nav-item { padding: 10px 14px; border-radius: 6px; cursor: pointer; color: var(--subtext); font-weight: 500; font-size: 0.95em; transition: all 0.2s; border: none; background: transparent; text-align: left; width: 100%; display: flex; align-items: center; gap: 10px; box-sizing: border-box; }
        .nav-item:hover, .nav-item.active { background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); }

        .main-content { flex: 1; overflow-y: auto; padding: 30px; box-sizing: border-box; }
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
        
        .chat-box { height: 350px; overflow-y: scroll; border: 1px solid var(--border-color); background: var(--input-bg); padding: 12px; border-radius: 8px; margin-bottom: 12px; }
        .chat-message { margin-bottom: 10px; font-size: 0.9em; line-height: 1.4; }
        h2, h3, h4 { margin-top: 0; font-weight: 600; letter-spacing: -0.01em; }
        a { color: var(--accent); text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <!-- AUTH CONTAINER -->
    <div id="authContainer" style="width:100%; height:100vh; display:flex; align-items:center; justify-content:center; background:var(--bg-color);">
        <div class="card" style="width: 100%; max-width: 420px; margin: 0;">
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
                <button onclick="registerAccount()">Sign Up</button>
                <p style="margin-top:16px; font-size:0.9em; text-align:center;">Already have an account? <a href="#" onclick="toggleAuth('login')">Sign in</a></p>
            </div>
        </div>
    </div>

    <!-- DASHBOARD CONTAINER -->
    <div id="dashboardContainer" class="hidden" style="display:flex; width:100%; height:100vh;">
        <!-- LEFT SIDEBAR TASKBAR -->
        <div class="sidebar">
            <div class="sidebar-brand">
                <div style="width:10px; height:10px; background:var(--accent); border-radius:50%;"></div>
                <span id="brandCompanyName">Tasko Workspace</span>
            </div>
            
            <div class="sidebar-menu">
                <button class="nav-item active" onclick="switchSection('tasksSection', this)">📋 Assigned Tasks</button>
                <button class="nav-item" onclick="switchSection('routinesSection', this)">🔄 Daily Routines</button>
                <button class="nav-item ceo-only hidden" onclick="switchSection('assignSection', this)">➕ Assign Task</button>
                <button class="nav-item ceo-only hidden" onclick="switchSection('routinesConfigSection', this)">⚙ Set Daily Routines</button>
                <button class="nav-item ceo-only hidden" onclick="switchSection('approvalsSection', this)">👥 Employee Approvals</button>
                <button class="nav-item" onclick="switchSection('chatSection', this)">💬 Team Chat</button>
                <button class="nav-item" onclick="switchSection('logsSection', this)">📜 Activity Logs</button>
                <button class="nav-item" onclick="switchSection('settingsSection', this)">⚙ Settings</button>
            </div>

            <div style="border-top:1px solid var(--border-color); padding-top:15px; margin-top:auto;">
                <div style="font-size:0.85em; color:var(--text-color); font-weight:600;" id="sidebarUserName">User</div>
                <div style="font-size:0.75em; color:var(--subtext); margin-bottom:10px;" id="sidebarUserRole">Role</div>
                <button onclick="logout()" style="background:#dc2626; padding:6px; font-size:0.85em;">Logout</button>
            </div>
        </div>

        <!-- MAIN CONTENT VIEW SECTIONS -->
        <div class="main-content">
            <!-- SECTION: ASSIGNED TASKS -->
            <div id="tasksSection" class="section-view">
                <div class="card">
                    <h2>Assigned Tasks & Routines</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Review your assigned tasks and upload image proofs for instant AI verification.</p>
                    <div id="myTasksList" style="margin-top:15px;">No tasks found.</div>
                </div>
            </div>

            <!-- SECTION: DAILY ROUTINES (Employee View / Status) -->
            <div id="routinesSection" class="section-view hidden">
                <div class="card">
                    <h2>Daily Recurring Routines</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">These routines reset every single day. Complete them daily!</p>
                    <div id="dailyRoutinesList" style="margin-top:15px;">No daily routines set for this workspace.</div>
                </div>
            </div>

            <!-- SECTION: ASSIGN TASK (CEO Only) -->
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
                    
                    <button onclick="createTask()" style="margin-top:10px;">Assign Task to Team Member</button>
                </div>
            </div>

            <!-- SECTION: SET DAILY ROUTINES (CEO Only) -->
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
                    
                    <button onclick="createDailyRoutine()" style="margin-top:10px;">Add Auto-Daily Routine</button>

                    <h4 style="margin-top:25px; font-size:0.95em; color:var(--subtext);">Active Daily Routines</h4>
                    <div id="configRoutinesList">No routines configured.</div>
                </div>
            </div>

            <!-- SECTION: EMPLOYEE APPROVALS (CEO Only) -->
            <div id="approvalsSection" class="section-view hidden">
                <div class="card">
                    <h2>Employee Approvals & Management</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Manage workspace access and active team members.</p>
                    
                    <h4 style="margin-top:20px; font-size:0.95em; color:var(--subtext);">Pending Requests</h4>
                    <div id="pendingUsersList" style="margin-bottom:20px;">No pending users.</div>

                    <h4 style="margin-top:20px; font-size:0.95em; color:var(--subtext);">Active Workspace Employees</h4>
                    <div id="employeesManageList">No employees in workspace.</div>
                </div>
            </div>

            <!-- SECTION: TEAM CHAT -->
            <div id="chatSection" class="section-view hidden">
                <div class="card" style="height:calc(100vh - 100px); display:flex; flex-direction:column;">
                    <h2>Live Workspace Chat</h2>
                    <div id="chatBox" class="chat-box" style="flex:1;"></div>
                    <div style="display:flex; gap:10px; margin-top:auto;">
                        <input type="text" id="chatInput" placeholder="Type a message to the team..." onkeydown="if(event.key==='Enter') sendChatMessage()" style="margin:0;">
                        <button onclick="sendChatMessage()" style="width: auto; margin:0; padding:0 24px;">Send</button>
                    </div>
                </div>
            </div>

            <!-- SECTION: ACTIVITY LOGS -->
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

            <!-- SECTION: SETTINGS -->
            <div id="settingsSection" class="section-view hidden">
                <div class="card" style="max-width: 600px;">
                    <h2>Workspace Settings</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Customize your workspace appearance and preferences.</p>
                    
                    <div style="margin-top:20px;">
                        <label style="font-size:0.85em; color:var(--subtext);">Workspace Company Name</label>
                        <div style="display:flex; gap:10px;">
                            <input type="text" id="settingsCompanyName" placeholder="MyCorp">
                            <button onclick="updateCompanyName()" style="width:auto; margin:6px 0 14px 0;">Save</button>
                        </div>
                    </div>

                    <div style="margin-top:10px;">
                        <label style="font-size:0.85em; color:var(--subtext);">Workspace ID (Read-only)</label>
                        <input type="text" id="settingsCompanyId" readonly style="opacity:0.7; cursor:not-allowed;">
                    </div>

                    <div style="margin-top:20px; border-top:1px solid var(--border-color); padding-top:20px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:8px;">Appearance Theme</label>
                        <button onclick="toggleTheme()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); width:auto; padding:8px 16px;">🌓 Toggle Dark / Light Mode</button>
                    </div>

                    <div style="margin-top:20px; border-top:1px solid var(--border-color); padding-top:20px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:8px;">Notification Sound Preference</label>
                        <select id="settingsNotificationSound">
                            <option value="enabled">Enabled (Chime on New Task)</option>
                            <option value="disabled">Disabled (Silent)</option>
                        </select>
                    </div>

                    <div style="margin-top:20px; border-top:1px solid var(--border-color); padding-top:20px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:8px;">Workspace Auto-Refresh Rate</label>
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
        let currentUser = JSON.parse(localStorage.getItem('tasko_user')) || null;
        let currentTheme = localStorage.getItem('tasko_theme') || 'dark';
        let savedRefreshRate = localStorage.getItem('tasko_refresh_rate') || '10';
        let workspaceRefreshInterval = null;

        window.onload = () => {
            if (currentTheme === 'light') { document.body.classList.add('light-theme'); }
            document.getElementById('settingsRefreshRate').value = savedRefreshRate;
            if (currentUser) {
                checkLoginState();
                setInterval(loadWorkspaceChat, 3000);
            }
        };

        function updateRefreshRateSetting() {
            savedRefreshRate = document.getElementById('settingsRefreshRate').value;
            localStorage.setItem('tasko_refresh_rate', savedRefreshRate);
            restartAutoRefreshInterval();
        }

        function restartAutoRefreshInterval() {
            if (workspaceRefreshInterval) {
                clearInterval(workspaceRefreshInterval);
            }
            let intervalMs = parseInt(savedRefreshRate) * 1000;
            workspaceRefreshInterval = setInterval(() => {
                if (!currentUser) return;
                loadDashboardData();
                if (currentUser.role === 'CEO') {
                    loadPendingUsers();
                    loadEmployeesManagement();
                }
            }, intervalMs);
        }

        function toggleTheme() {
            document.body.classList.toggle('light-theme');
            currentTheme = document.body.classList.contains('light-theme') ? 'light' : 'dark';
            localStorage.setItem('tasko_theme', currentTheme);
        }

        function switchSection(sectionId, btnElement) {
            document.querySelectorAll('.section-view').forEach(el => el.classList.add('hidden'));
            document.getElementById(sectionId).classList.remove('hidden');
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            btnElement.classList.add('active');
        }

        function toggleAuth(formType) {
            document.getElementById('loginForm').classList.toggle('hidden', formType !== 'login');
            document.getElementById('signupForm').classList.toggle('hidden', formType !== 'signup');
        }

        function toggleCompanyInput() {
            let role = document.getElementById('suRole').value;
            let isCeo = role === 'CEO';
            document.getElementById('suCompanyName').classList.toggle('hidden', !isCeo);
            document.getElementById('suCompanyId').classList.toggle('hidden', isCeo);
        }

        async function registerAccount() {
            let signupData = {
                name: document.getElementById('suName').value,
                email: document.getElementById('suEmail').value,
                password: document.getElementById('suPassword').value,
                role: document.getElementById('suRole').value,
                company_name: document.getElementById('suCompanyName').value,
                company_id: document.getElementById('suCompanyId').value
            };
            if(!signupData.name || !signupData.email || !signupData.password) {
                alert("Please fill out all fields.");
                return;
            }
            let res = await fetch('/api/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(signupData)});
            let result = await res.json();
            if(res.ok) {
                alert("Account created successfully!");
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
            if (workspaceRefreshInterval) clearInterval(workspaceRefreshInterval);
            document.getElementById('authContainer').style.display = 'flex';
            document.getElementById('dashboardContainer').classList.add('hidden');
            toggleAuth('login');
        }

        async function checkLoginState() {
            if(!currentUser) return;
            document.getElementById('authContainer').style.display = 'none';
            document.getElementById('dashboardContainer').classList.remove('hidden');
            
            document.getElementById('sidebarUserName').innerText = currentUser.name;
            document.getElementById('sidebarUserRole').innerText = currentUser.role;
            document.getElementById('brandCompanyName').innerText = currentUser.company_name || "Tasko Workspace";
            
            document.getElementById('settingsCompanyName').value = currentUser.company_name || "MyCorp";
            document.getElementById('settingsCompanyId').value = currentUser.company_id;

            let isCeo = currentUser.role === 'CEO';
            document.querySelectorAll('.ceo-only').forEach(el => {
                if(isCeo) el.classList.remove('hidden');
                else el.classList.add('hidden');
            });

            if(isCeo) {
                loadPendingUsers();
                loadEmployeesManagement();
                loadEmployeesDropdown();
            }
            loadDashboardData();
            loadWorkspaceChat();
            restartAutoRefreshInterval();
        }

        async function updateCompanyName() {
            let newName = document.getElementById('settingsCompanyName').value;
            if(!newName) { alert("Company name cannot be empty."); return; }
            let res = await fetch('/api/admin/update-company', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({company_id: currentUser.company_id, company_name: newName})});
            if(res.ok) {
                currentUser.company_name = newName;
                localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                document.getElementById('brandCompanyName').innerText = newName;
                alert("Company name updated successfully!");
            }
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
            let opts = '<option value="">Select Employee</option>' + data.employees.map(e => `<option value="${e.name}">${e.name} (${e.role})</option>`).join('');
            document.getElementById('taskAssigneeSelect').innerHTML = opts;
            document.getElementById('routineAssigneeSelect').innerHTML = opts;
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
            alert("Task assigned successfully!");
            loadDashboardData();
        }

        async function createDailyRoutine() {
            let data = {
                title: document.getElementById('routineTitle').value,
                assigned_to: document.getElementById('routineAssigneeSelect').value,
                company_id: currentUser.company_id
            };
            if(!data.title || !data.assigned_to) {
                alert("Please fill out routine title and assignee.");
                return;
            }
            await fetch('/api/routines', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            document.getElementById('routineTitle').value = '';
            alert("Daily routine configured successfully!");
            loadDashboardData();
        }

        async function loadDashboardData() {
            let res = await fetch(`/api/data?company_id=${currentUser.company_id}`);
            let data = await res.json();
            
            // Tasks
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

            // Daily Routines
            let routinesContainer = document.getElementById('dailyRoutinesList');
            let configRoutinesContainer = document.getElementById('configRoutinesList');
            let userRoutines = data.routines.filter(r => String(r.company_id) === String(currentUser.company_id));

            if(configRoutinesContainer) {
                configRoutinesContainer.innerHTML = userRoutines.length === 0 ? "<span style='color:var(--subtext);'>No daily routines configured.</span>" :
                    userRoutines.map(r => `<div style="padding:6px 0; border-bottom:1px solid var(--border-color);"><b>${r.title}</b> &bull; Assigned to: ${r.assigned_to}</div>`).join('');
            }

            let viewableRoutines = userRoutines.filter(r => currentUser.role === 'CEO' || r.assigned_to === currentUser.name);
            if(viewableRoutines.length === 0) {
                routinesContainer.innerHTML = "<span style='color:var(--subtext);'>No daily routines for today.</span>";
            } else {
                routinesContainer.innerHTML = viewableRoutines.map(r => {
                    let rIdx = data.routines.indexOf(r);
                    let isDoneToday = r.today_status === 'Completed';
                    let statusBadge = isDoneToday ? '<span class="badge-done">Done Today ✓</span>' : '<span class="badge-pending">Pending Today</span>';
                    
                    let actionHtml = '';
                    if(currentUser.role !== 'CEO' && !isDoneToday) {
                        actionHtml = `
                            <div style="margin-top:10px;">
                                <button onclick="markRoutineDone(${rIdx})" style="background:#059669; width:auto; padding:6px 14px; font-size:0.85em;">Mark Completed for Today</button>
                            </div>
                        `;
                    }

                    return `
                        <div style="background:var(--input-bg); padding:14px; margin:10px 0; border-radius:8px; border:1px solid var(--border-color);">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <div>
                                    <strong>${r.title}</strong>
                                    <div style="font-size:0.85em; color:var(--subtext);">Assigned to: ${r.assigned_to} &bull; Resets daily</div>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                            ${actionHtml}
                        </div>
                    `;
                }).join('');
            }

            // History
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
                try {
                    let res = await fetch('/api/tasks/verify-proof', {
                        method: 'POST', 
                        headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({index: taskIndex, image: base64Image, mime_type: file.type})
                    });
                    let result = await res.json();
                    if(res.ok) { 
                        alert("AI Verification Complete: " + result.status); 
                        loadDashboardData(); 
                    } else {
                        alert("Error: " + (result.error || "Verification failed"));
                    }
                } catch(err) {
                    alert("Network error while submitting proof.");
                }
            };
        }

        async function markRoutineDone(routineIndex) {
            await fetch('/api/routines/complete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index: routineIndex})});
            loadDashboardData();
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


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    email = data.get("email")
    if email in USERS_DB or email in PENDING_USERS_DB:
        return jsonify({"error": "Email already registered."}), 400

    name = data.get("name")
    password = data.get("password")
    role = data.get("role", "Employee")

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
    user = USERS_DB.get(data.get("email")) or PENDING_USERS_DB.get(data.get("email"))
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
        "tasks": [t for t in TASKS_DB if str(t.get("company_id")) == str(company_id)],
        "routines": routines_with_status,
        "history": [h for h in HISTORY_DB if str(h.get("company_id")) == str(company_id) or "company_id" not in h],
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
            "action": f"Task '{title}' assigned to {assigned_to} (Deadline: {deadline or 'None'})",
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

    DAILY_ROUTINES_DB.append({
        "title": title,
        "assigned_to": assigned_to,
        "company_id": company_id,
    })

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
            "action": f"Daily routine '{routine['title']}' marked completed for today by {routine['assigned_to']}",
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
            f"Evaluate if the uploaded image proves task completion for: '{task['title']}'."
            " Respond with your evaluation clearly stating whether it is Completed or Failed, followed by a brief reason."
        )
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        ai_text = response.text or "Completed successfully."
        
        task["status"] = "Completed" if "Completed" in ai_text or "pass" in ai_text.lower() else "Failed"
        task["ai_comment"] = ai_text

        HISTORY_DB.insert(
            0,
            {
                "company_id": task.get("company_id"),
                "action": f"AI verified task '{task['title']}' for {task['assigned_to']}: {task['status']}",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
        )
        return jsonify({"success": True, "status": task["status"], "comment": ai_text})
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
        filtered_msgs = [m for m in MESSAGES_DB if str(m.get("company_id")) == str(company_id)]
        return jsonify({"messages": filtered_msgs})


@app.route("/api/admin/pending-users", methods=["GET"])
def get_pending_users():
    company_id = request.args.get("company_id")
    return jsonify({
        "pending_users": [u for u in PENDING_USERS_DB.values() if str(u.get("company_id")) == str(company_id)]
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
            if str(u.get("company_id")) == str(company_id) and u.get("status") == "Approved"
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