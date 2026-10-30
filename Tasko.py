import datetime
import os
import base64
import random
import io
import json
from flask import Flask, jsonify, request, render_template_string, send_file
from google import genai
from google.api_core import exceptions
from google.genai import types

app = Flask(__name__)

# Correctly initialize the Google GenAI client using GEMINI_API_KEY
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY")) if os.environ.get("GEMINI_API_KEY") else None

# --- Persistent Storage Layer ---
DB_DIR = "tasko_data"
os.makedirs(DB_DIR, exist_ok=True)

def load_db(filename, default):
    path = os.path.join(DB_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_db(filename, data):
    path = os.path.join(DB_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f)

USERS_DB = load_db("users.json", {})
PENDING_USERS_DB = load_db("pending_users.json", {})
TASKS_DB = load_db("tasks.json", [])
DAILY_ROUTINES_DB = load_db("routines.json", [])
DAILY_LOGS_DB = load_db("daily_logs.json", {})
HISTORY_DB = load_db("history.json", [])
MESSAGES_DB = load_db("messages.json", [])

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Tasko - Workspace Manager</title>
    <!-- Favicon and Touch Icon Links -->
    <link rel="icon" type="image/png" href="/favicon.ico">
    <link rel="apple-touch-icon" href="/favicon.ico">
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
            --ease-linear: cubic-bezier(0.16, 1, 0.3, 1);
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
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; display: flex; height: 100vh; overflow: hidden; transition: background 0.3s var(--ease-linear), color 0.3s var(--ease-linear); }
        
        .sidebar { width: 265px; min-width: 265px; background: var(--sidebar-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 24px; box-sizing: border-box; height: 100vh; }
        .sidebar-brand { font-size: 1.15em; font-weight: 600; display: flex; align-items: center; gap: 10px; margin-bottom: 32px; color: var(--text-color); letter-spacing: -0.01em; }
        .sidebar-menu { display: flex; flex-direction: column; gap: 6px; flex: 1; overflow-y: auto; }
        
        .nav-item { padding: 10px 14px; border-radius: 8px; cursor: pointer; color: var(--subtext); font-weight: 500; font-size: 0.9em; transition: all 0.2s var(--ease-linear); border: none; background: transparent; text-align: left; width: 100%; display: flex; align-items: center; gap: 10px; box-sizing: border-box; position: relative; }
        .nav-item:hover { background: rgba(255,255,255,0.04); color: var(--text-color); transform: translateX(3px); }
        .nav-item.active { background: var(--card-bg); color: var(--text-color); border: 1px solid var(--border-color); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }

        .main-content { flex: 1; overflow-y: auto; padding: 48px; box-sizing: border-box; }
        
        @keyframes linearEntrance {
            from { opacity: 0; transform: translateY(10px) scale(0.99); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        .section-view { animation: linearEntrance 0.3s var(--ease-linear) forwards; }

        .card { background: var(--card-bg); padding: 32px; border-radius: 14px; margin-bottom: 24px; box-shadow: 0 8px 30px -6px rgba(0,0,0,0.5); border: 1px solid var(--border-color); transition: all 0.25s var(--ease-linear); position: relative; overflow: hidden; }
        .card:hover { border-color: rgba(94, 106, 210, 0.5); transform: translateY(-2px); box-shadow: 0 12px 40px -8px rgba(94, 106, 210, 0.2); }
        
        input, select, textarea { padding: 12px 16px; margin: 8px 0 18px 0; border-radius: 8px; border: 1px solid var(--border-color); background: var(--input-bg); color: var(--text-color); width: 100%; box-sizing: border-box; font-size: 0.9em; outline: none; transition: all 0.2s var(--ease-linear); }
        input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(94, 106, 210, 0.15); background: var(--card-bg); }
        
        button { background: var(--accent); cursor: pointer; font-weight: 500; border: none; color: white; padding: 12px 20px; border-radius: 8px; width: 100%; font-size: 0.9em; transition: all 0.2s var(--ease-linear); display: inline-flex; align-items: center; justify-content: center; gap: 8px; box-shadow: 0 4px 14px rgba(94, 106, 210, 0.4); }
        button:hover { background: var(--accent-hover); transform: translateY(-2px); box-shadow: 0 6px 20px rgba(94, 106, 210, 0.5); }
        button:active { transform: translateY(0) scale(0.98); box-shadow: 0 2px 8px rgba(94, 106, 210, 0.3); }
        
        .hidden { display: none !important; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9em; }
        th, td { padding: 14px; border-bottom: 1px solid var(--border-color); text-align: left; }
        th { background: var(--input-bg); color: var(--subtext); font-weight: 600; text-transform: uppercase; font-size: 0.75em; letter-spacing: 0.05em; }
        
        .badge-done { background: rgba(5, 150, 105, 0.15); color: #34d399; border: 1px solid rgba(5, 150, 105, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .badge-pending { background: rgba(217, 119, 6, 0.15); color: #fbbf24; border: 1px solid rgba(217, 119, 6, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .badge-failed { background: rgba(220, 38, 38, 0.15); color: #f87171; border: 1px solid rgba(220, 38, 38, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        
        @keyframes pulseGlow {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.05); opacity: 1; box-shadow: 0 0 12px currentColor; }
            100% { transform: scale(0.95); opacity: 0.8; }
        }
        .pulsing-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; animation: pulseGlow 2s infinite ease-in-out; }

        #toastContainer { position: fixed; top: 24px; right: 24px; z-index: 9999; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
        .toast { background: var(--card-bg); border: 1px solid var(--border-color); color: var(--text-color); padding: 14px 20px; border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); font-size: 0.9em; display: flex; align-items: center; gap: 12px; pointer-events: auto; animation: toastIn 0.3s var(--ease-linear) forwards; min-width: 280px; }
        .toast.success { border-left: 4px solid #34d399; }
        .toast.error { border-left: 4px solid #f87171; }
        .toast.info { border-left: 4px solid var(--accent); }
        @keyframes toastIn {
            from { opacity: 0; transform: translateY(-15px) scale(0.95); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes toastOut {
            from { opacity: 1; transform: translateY(0) scale(1); }
            to { opacity: 0; transform: translateY(-15px) scale(0.95); }
        }

        .chat-box { height: 400px; overflow-y: scroll; border: 1px solid var(--border-color); background: var(--input-bg); padding: 18px; border-radius: 10px; margin-bottom: 16px; scroll-behavior: smooth; }
        .chat-message { margin-bottom: 14px; font-size: 0.9em; line-height: 1.5; animation: linearEntrance 0.25s var(--ease-linear); }
        h2, h3, h4 { margin-top: 0; font-weight: 600; letter-spacing: -0.01em; }
        a { color: var(--accent); text-decoration: none; transition: color 0.15s; }
        a:hover { color: var(--accent-hover); text-decoration: underline; }
    </style>
</head>
<body>
    <div id="toastContainer"></div>

    <div id="authContainer" style="width:100%; height:100vh; display:flex; align-items:center; justify-content:center; background:var(--bg-color);">
        <div class="card" style="width: 100%; max-width: 420px; margin: 0; animation: linearEntrance 0.4s var(--ease-linear);">
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:28px;">
                <div style="width:10px; height:10px; background:var(--accent); border-radius:50%; box-shadow: 0 0 16px var(--accent);"></div>
                <h2 style="margin:0; font-size: 1.3em; letter-spacing:-0.02em;">Tasko Workspace</h2>
            </div>
            
            <div id="loginForm">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;">Sign in to your account</h3>
                <input type="email" id="loginEmail" placeholder="name@company.com">
                <input type="password" id="loginPassword" placeholder="••••••••••••">
                <button onclick="login()">Continue to Workspace</button>
                <p style="margin-top:22px; font-size:0.85em; text-align:center; color:var(--subtext);">Don't have an account? <a href="#" onclick="toggleAuth('signup')">Sign up</a></p>
            </div>

            <div id="signupForm" class="hidden">
                <h3 style="color:var(--subtext); font-size:0.9em; margin-bottom:18px; font-weight: 500;">Create a new account</h3>
                <input type="text" id="suName" placeholder="Full Name">
                <input type="email" id="suEmail" placeholder="name@company.com">
                <input type="password" id="suPassword" placeholder="••••••••••••">
                <select id="suRole" onchange="toggleCompanyInput()">
                    <option value="Employee">Employee</option>
                    <option value="CEO">CEO (Create Workspace)</option>
                </select>
                <input type="text" id="suCompanyName" placeholder="Company Name" class="hidden">
                <input type="text" id="suCompanyId" placeholder="Workspace Company ID">
                <button onclick="registerAccount()">Create Account</button>
                <p style="margin-top:22px; font-size:0.85em; text-align:center; color:var(--subtext);">Already have an account? <a href="#" onclick="toggleAuth('login')">Sign in</a></p>
            </div>
        </div>
    </div>

    <div id="waitingContainer" class="hidden" style="width:100%; height:100vh; display:flex; align-items:center; justify-content:center; background:var(--bg-color);">
        <div class="card" style="width: 100%; max-width: 420px; margin: 0; text-align:center; padding: 44px 32px; animation: linearEntrance 0.4s var(--ease-linear);">
            <div style="width:12px; height:12px; background:#fbbf24; border-radius:50%; margin:0 auto 20px auto; box-shadow: 0 0 16px #fbbf24; animation: pulseGlow 1.5s infinite;"></div>
            <h2 style="margin-bottom:12px;">Waiting for CEO Approval</h2>
            <p style="color:var(--subtext); font-size:0.9em; line-height:1.6; margin-bottom:32px;">Your account has been registered. The workspace CEO needs to review and assign your role/permissions before you can enter.</p>
            <button onclick="logout()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); box-shadow:none;">Sign Out</button>
        </div>
    </div>

    <div id="dashboardContainer" class="hidden" style="display:flex; width:100%; height:100vh;">
        <div class="sidebar">
            <div class="sidebar-brand">
                <div style="width:8px; height:8px; background:var(--accent); border-radius:50%; box-shadow: 0 0 10px var(--accent);"></div>
                <span id="brandCompanyName">Tasko Workspace</span>
            </div>
            
            <div class="sidebar-menu">
                <button class="nav-item active" onclick="switchSection('tasksSection', this)">📋 Assigned Tasks</button>
                <button class="nav-item" onclick="switchSection('routinesSection', this)">🔄 Daily Routines</button>
                <button id="navAssign" class="nav-item ceo-only hidden" onclick="switchSection('assignSection', this)">➕ Assign Task</button>
                <button id="navRoutinesConfig" class="nav-item ceo-only hidden" onclick="switchSection('routinesConfigSection', this)">⚙ Set Daily Routines</button>
                <button class="nav-item ceo-only hidden" onclick="switchSection('approvalsSection', this)">👥 Employee Approvals</button>
                <button id="navChat" class="nav-item" onclick="switchSection('chatSection', this)">💬 Team Chat</button>
                <button id="navLogs" class="nav-item" onclick="switchSection('logsSection', this)">📜 Activity Logs</button>
                <button class="nav-item" onclick="switchSection('settingsSection', this)">⚙ Settings</button>
            </div>

            <div style="border-top:1px solid var(--border-color); padding-top:16px; margin-top:auto;">
                <div style="font-size:0.85em; color:var(--text-color); font-weight:600;" id="sidebarUserName">User</div>
                <div style="font-size:0.75em; color:var(--subtext); margin-bottom:4px;" id="sidebarUserRole">Role</div>
                <div style="font-size:0.75em; color:var(--accent); margin-bottom:14px;" id="sidebarUserStance">Stance</div>
                <button onclick="logout()" style="background:rgba(220,38,38,0.12); color:#f87171; border:1px solid rgba(220,38,38,0.25); padding:8px; font-size:0.85em; box-shadow:none;">Logout</button>
            </div>
        </div>

        <div class="main-content">
            <div id="tasksSection" class="section-view">
                <div class="card">
                    <h2>Assigned Tasks & Routines</h2>
                    <p style="font-size:0.9em; color:var(--subtext);">Review your assigned tasks and upload image proofs for instant AI verification.</p>
                    <div id="myTasksList" style="margin-top:20px;">No tasks found.</div>
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
                    
                    <button onclick="createTask()" style="margin-top:12px;">Assign Task to Team Member</button>
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
                    
                    <button onclick="createDailyRoutine()" style="margin-top:12px;">Add Auto-Daily Routine</button>

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
                        <button onclick="sendChatMessage()" style="width: auto; margin:0; padding:0 28px;">Send</button>
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
                            <button onclick="updateCompanyName()" style="width:auto; margin:8px 0 18px 0; padding:0 20px;">Save</button>
                        </div>
                    </div>

                    <div style="margin-top:12px;">
                        <label style="font-size:0.85em; color:var(--subtext);">Workspace ID (Read-only)</label>
                        <input type="text" id="settingsCompanyId" readonly style="opacity:0.7; cursor:not-allowed;">
                    </div>

                    <div style="margin-top:24px; border-top:1px solid var(--border-color); padding-top:24px;">
                        <label style="font-size:0.85em; color:var(--subtext); display:block; margin-bottom:10px;">Appearance Theme</label>
                        <button onclick="toggleTheme()" style="background:var(--input-bg); border:1px solid var(--border-color); color:var(--text-color); width:auto; padding:10px 18px; box-shadow:none;">🌓 Toggle Dark / Light Mode</button>
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
        let currentUser = JSON.parse(localStorage.getItem('tasko_user')) || null;
        let currentTheme = localStorage.getItem('tasko_theme') || 'dark';
        let savedRefreshRate = localStorage.getItem('tasko_refresh_rate') || '10';
        let workspaceRefreshInterval = null;
        let approvalCheckInterval = null;

        window.onload = () => {
            if (currentTheme === 'light') { document.body.classList.add('light-theme'); }
            document.getElementById('settingsRefreshRate').value = savedRefreshRate;
            if (currentUser) {
                checkLoginState();
                setInterval(loadWorkspaceChat, 3000);
            }
        };

        function showToast(message, type = 'success') {
            let container = document.getElementById('toastContainer');
            let toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.animation = 'toastOut 0.3s var(--ease-linear) forwards';
                setTimeout(() => toast.remove(), 300);
            }, 3000);
        }

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
            let target = document.getElementById(sectionId);
            target.classList.remove('hidden');
            
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
                showToast("Please fill out all fields.", "error");
                return;
            }
            let res = await fetch('/api/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(signupData)});
            let result = await res.json();
            if(res.ok) {
                currentUser = result.user;
                localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                showToast("Account created successfully!");
                checkLoginState();
            } else { showToast(result.error, "error"); }
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
                showToast("Logged in successfully!");
                checkLoginState();
            } else { showToast(result.error, "error"); }
        }

        function logout() {
            currentUser = null;
            localStorage.removeItem('tasko_user');
            if (workspaceRefreshInterval) clearInterval(workspaceRefreshInterval);
            if (approvalCheckInterval) clearInterval(approvalCheckInterval);
            document.getElementById('authContainer').style.display = 'flex';
            document.getElementById('waitingContainer').classList.add('hidden');
            document.getElementById('dashboardContainer').classList.add('hidden');
            toggleAuth('login');
            showToast("Logged out successfully.", "info");
        }

        async function checkLoginState() {
            if(!currentUser) return;

            if(currentUser.role === 'Employee') {
                let statusRes = await fetch(`/api/check-status?email=${currentUser.email}`);
                let statusData = await statusRes.json();
                if(statusData.status === 'Pending') {
                    document.getElementById('authContainer').style.display = 'none';
                    document.getElementById('dashboardContainer').classList.add('hidden');
                    document.getElementById('waitingContainer').classList.remove('hidden');
                    
                    if(!approvalCheckInterval) {
                        approvalCheckInterval = setInterval(checkLoginState, 5000);
                    }
                    return;
                } else if(statusData.status === 'Approved') {
                    if(approvalCheckInterval) clearInterval(approvalCheckInterval);
                    currentUser.status = 'Approved';
                    currentUser.company_name = statusData.company_name;
                    currentUser.stance = statusData.stance;
                    currentUser.permissions = statusData.permissions || [];
                    currentUser.other_permission = statusData.other_permission || "";
                    localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                }
            }

            document.getElementById('authContainer').style.display = 'none';
            document.getElementById('waitingContainer').classList.add('hidden');
            document.getElementById('dashboardContainer').classList.remove('hidden');
            
            document.getElementById('sidebarUserName').innerText = currentUser.name;
            document.getElementById('sidebarUserRole').innerText = currentUser.role;
            document.getElementById('sidebarUserStance').innerText = currentUser.stance ? `Stance: ${currentUser.stance}` : '';
            document.getElementById('brandCompanyName').innerText = currentUser.company_name || "Tasko Workspace";
            
            document.getElementById('settingsCompanyName').value = currentUser.company_name || "MyCorp";
            document.getElementById('settingsCompanyId').value = currentUser.company_id;

            let isCeo = currentUser.role === 'CEO';
            let permissions = currentUser.permissions || [];

            document.querySelectorAll('.ceo-only').forEach(el => {
                if(isCeo) el.classList.remove('hidden');
                else el.classList.add('hidden');
            });

            if(!isCeo) {
                document.getElementById('navAssign').classList.toggle('hidden', !permissions.includes('assign_tasks'));
                document.getElementById('navRoutinesConfig').classList.toggle('hidden', !permissions.includes('config_routines'));
                document.getElementById('navLogs').classList.toggle('hidden', !permissions.includes('view_logs'));
            }

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
            if(!newName) { showToast("Company name cannot be empty.", "error"); return; }
            let res = await fetch('/api/admin/update-company', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({company_id: currentUser.company_id, company_name: newName})});
            if(res.ok) {
                currentUser.company_name = newName;
                localStorage.setItem('tasko_user', JSON.stringify(currentUser));
                document.getElementById('brandCompanyName').innerText = newName;
                showToast("Company name updated successfully!");
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
                <div style="background:var(--input-bg); padding:16px; margin:12px 0; border-radius:10px; border:1px solid var(--border-color);">
                    <div style="margin-bottom:8px;"><strong>${u.name}</strong> (${u.email})</div>
                    
                    <label style="font-size:0.8em; color:var(--subtext);">Select Stance / Type Custom Role:</label>
                    <select id="stanceSelect_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}" onchange="if(this.value==='Custom') document.getElementById('customStance_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}').classList.remove('hidden'); else document.getElementById('customStance_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}').classList.add('hidden');" style="margin-bottom:8px;">
                        <option value="Team Member">Team Member</option>
                        <option value="Lead Developer">Lead Developer</option>
                        <option value="Operations Manager">Operations Manager</option>
                        <option value="Marketing Specialist">Marketing Specialist</option>
                        <option value="Custom">Custom Role...</option>
                    </select>
                    <input type="text" id="customStance_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}" placeholder="Enter custom stance..." class="hidden" style="margin-bottom:10px;">

                    <label style="font-size:0.8em; color:var(--subtext); display:block; margin-bottom:6px;">Permissions Checklist:</label>
                    <div style="display:flex; flex-direction:column; gap:6px; font-size:0.85em; margin-bottom:12px;">
                        <label><input type="checkbox" id="perm_assign_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}" checked> Can Assign Tasks</label>
                        <label><input type="checkbox" id="perm_routines_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}"> Can Configure Daily Routines</label>
                        <label><input type="checkbox" id="perm_logs_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}" checked> Can View Activity Logs</label>
                    </div>

                    <label style="font-size:0.8em; color:var(--subtext);">Other Permission (AI-assisted custom grant):</label>
                    <div style="display:flex; gap:8px; margin-top:4px; margin-bottom:12px;">
                        <input type="text" id="otherPermInput_${u.email.replace(/[^a-zA-Z0-9]/g, '_')}" placeholder="e.g. Access financial records or manage billing..." style="margin:0;">
                        <button onclick="generateAIPermission('${u.email}')" style="background:var(--card-bg); border:1px solid var(--border-color); color:var(--accent); width:auto; padding:0 14px; font-size:0.8em; box-shadow:none;">✨ AI Enhance</button>
                    </div>

                    <button onclick="approveUser('${u.email}')" style="width:auto; padding:8px 16px; font-size:0.85em;">Approve Employee</button>
                </div>
            `).join('');
        }

        async function generateAIPermission(email) {
            let emailKey = email.replace(/[^a-zA-Z0-9]/g, '_');
            let inputField = document.getElementById(`otherPermInput_${emailKey}`);
            let promptText = inputField.value.trim();
            if(!promptText) {
                showToast("Please type a rough note for the custom permission first.", "error");
                return;
            }
            showToast("Generating professional permission with Gemini AI...", "info");
            let res = await fetch('/api/ai/enhance-permission', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ prompt: promptText })
            });
            let result = await res.json();
            if(res.ok) {
                inputField.value = result.enhanced;
                showToast("Permission refined by Gemini AI!");
            } else {
                showToast("AI enhancement failed.", "error");
            }
        }

        async function approveUser(email) {
            let emailKey = email.replace(/[^a-zA-Z0-9]/g, '_');
            let stanceDropdown = document.getElementById(`stanceSelect_${emailKey}`).value;
            let stance = stanceDropdown === 'Custom' ? document.getElementById(`customStance_${emailKey}`).value : stanceDropdown;
            if(!stance) stance = "Team Member";

            let permissions = [];
            if(document.getElementById(`perm_assign_${emailKey}`).checked) permissions.push('assign_tasks');
            if(document.getElementById(`perm_routines_${emailKey}`).checked) permissions.push('config_routines');
            if(document.getElementById(`perm_logs_${emailKey}`).checked) permissions.push('view_logs');

            let otherPermission = document.getElementById(`otherPermInput_${emailKey}`).value.trim();

            await fetch('/api/admin/approve-user', {
                method: 'POST', 
                headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify({ email, stance, permissions, other_permission: otherPermission })
            });
            showToast("Employee approved successfully!");
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
                <div style="display:flex; justify-content:space-between; align-items:center; padding:12px 0; border-bottom:1px solid var(--border-color);">
                    <div>
                        <span>${e.name} (${e.email}) &bull; <b style="color:var(--accent);">${e.stance || e.role}</b></span>
                        ${e.other_permission ? `<div style="font-size:0.8em; color:var(--subtext); margin-top:2px;">Custom Permission: ${e.other_permission}</div>` : ''}
                    </div>
                    <button onclick="kickEmployee('${e.email}')" style="width:auto; padding:6px 14px; font-size:0.85em; background:rgba(220,38,38,0.15); color:#f87171; border:1px solid rgba(220,38,38,0.3); box-shadow:none;">Kick</button>
                </div>
            `).join('');
        }

        async function kickEmployee(email) {
            if(!confirm("Are you sure you want to kick this employee?")) return;
            await fetch('/api/admin/kick-employee', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email, company_id: currentUser.company_id})});
            showToast("Employee removed from workspace.", "info");
            loadEmployeesManagement();
            loadEmployeesDropdown();
            loadDashboardData();
        }

        async function loadEmployeesDropdown() {
            let res = await fetch(`/api/admin/employees-list?company_id=${currentUser.company_id}`);
            let data = await res.json();
            let opts = '<option value="">Select Employee</option>' + data.employees.map(e => `<option value="${e.name}">${e.name} (${e.stance || e.role})</option>`).join('');
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
                showToast("Please fill out task title and assignee.", "error");
                return;
            }
            await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            document.getElementById('taskTitle').value = '';
            document.getElementById('taskDeadline').value = '';
            showToast("Task assigned successfully!");
            loadDashboardData();
        }

        async function createDailyRoutine() {
            let data = {
                title: document.getElementById('routineTitle').value,
                assigned_to: document.getElementById('routineAssigneeSelect').value,
                company_id: currentUser.company_id
            };
            if(!data.title || !data.assigned_to) {
                showToast("Please fill out routine title and assignee.", "error");
                return;
            }
            await fetch('/api/routines', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
            document.getElementById('routineTitle').value = '';
            showToast("Daily routine configured successfully!");
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
                    let globalIdx = data.tasks.indexOf(t);
                    let statusBadge = t.status === 'Completed' ? '<span class="badge-done"><span class="pulsing-dot"></span>Completed</span>' : 
                                      t.status === 'Failed' ? '<span class="badge-failed"><span class="pulsing-dot"></span>Failed</span>' : 
                                      '<span class="badge-pending"><span class="pulsing-dot"></span>Pending Proof</span>';
                    
                    let actionHtml = '';
                    if(currentUser.role !== 'CEO') {
                        actionHtml = `
                            <div style="margin-top: 16px; border-top: 1px solid var(--border-color); padding-top: 16px;" id="taskProofBox_${globalIdx}">
                                <label style="font-size:0.85em; display:block; margin-bottom:8px; color:var(--subtext);">Upload Proof Image for AI Verification:</label>
                                <input type="file" id="proofFile_${globalIdx}" accept="image/*" style="margin-bottom:10px; padding:8px;">
                                <button onclick="submitProof(${globalIdx})" style="background:#059669; padding:10px 16px; font-size:0.9em; width:auto;">Submit Proof</button>
                            </div>
                        `;
                    }

                    return `
                        <div style="background:var(--input-bg); padding:20px; margin:14px 0; border-radius:10px; border:1px solid var(--border-color); transition: all 0.2s var(--ease-linear);">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                                <div>
                                    <strong style="font-size:1.1em; letter-spacing:-0.01em;">${t.title}</strong>
                                    <div style="font-size:0.85em; color:var(--subtext); margin-top:6px;">
                                        Assigned to: ${t.assigned_to} &bull; Priority: ${t.priority} &bull; Deadline: ${t.deadline || 'None'}
                                    </div>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                            ${t.ai_comment ? `<div style="margin-top:12px; font-size:0.85em; color:var(--subtext); background:var(--card-bg); padding:12px; border-radius:8px; border: 1px solid var(--border-color);"><strong>AI Feedback:</strong> ${t.ai_comment}</div>` : ''}
                            ${actionHtml}
                        </div>
                    `;
                }).join('');
            }

            let routinesContainer = document.getElementById('dailyRoutinesList');
            let configRoutinesContainer = document.getElementById('configRoutinesList');
            let userRoutines = data.routines.filter(r => String(r.company_id) === String(currentUser.company_id));

            if(configRoutinesContainer) {
                configRoutinesContainer.innerHTML = userRoutines.length === 0 ? "<span style='color:var(--subtext);'>No daily routines configured.</span>" :
                    userRoutines.map(r => `<div style="padding:10px 0; border-bottom:1px solid var(--border-color);"><b>${r.title}</b> &bull; Assigned to: ${r.assigned_to}</div>`).join('');
            }

            let viewableRoutines = userRoutines.filter(r => currentUser.role === 'CEO' || r.assigned_to === currentUser.name);
            if(viewableRoutines.length === 0) {
                routinesContainer.innerHTML = "<span style='color:var(--subtext);'>No daily routines for today.</span>";
            } else {
                routinesContainer.innerHTML = viewableRoutines.map(r => {
                    let rIdx = data.routines.indexOf(r);
                    let isDoneToday = r.today_status === 'Completed';
                    let statusBadge = isDoneToday ? '<span class="badge-done"><span class="pulsing-dot"></span>Done Today ✓</span>' : '<span class="badge-pending"><span class="pulsing-dot"></span>Pending Today</span>';
                    
                    let actionHtml = '';
                    if(currentUser.role !== 'CEO' && !isDoneToday) {
                        actionHtml = `
                            <div style="margin-top:12px;">
                                <button onclick="markRoutineDone(${rIdx})" style="background:#059669; width:auto; padding:8px 16px; font-size:0.85em;">Mark Completed for Today</button>
                            </div>
                        `;
                    }

                    return `
                        <div style="background:var(--input-bg); padding:20px; margin:14px 0; border-radius:10px; border:1px solid var(--border-color);">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <div>
                                    <strong style="font-size:1.05em; letter-spacing:-0.01em;">${r.title}</strong>
                                    <div style="font-size:0.85em; color:var(--subtext); margin-top:4px;">Assigned to: ${r.assigned_to} &bull; Resets daily</div>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                            ${actionHtml}
                        </div>
                    `;
                }).join('');
            }

            let historyBody = document.getElementById('historyTableBody');
            let workspaceHistory = data.history.filter(h => !h.company_id || String(h.company_id) === String(currentUser.company_id));
            historyBody.innerHTML = workspaceHistory.length === 0 ? `<tr><td colspan="2" style="color:var(--subtext);">No history recorded yet.</td></tr>` : 
                workspaceHistory.map(h => `<tr><td style="color:var(--subtext); width:150px;">${h.time}</td><td>${h.action}</td></tr>`).join('');
        }

        async function submitProof(taskIndex) {
            let fileInput = document.getElementById(`proofFile_${taskIndex}`);
            if(!fileInput || fileInput.files.length === 0) { 
                showToast("Please select an image file.", "error"); 
                return; 
            }
            let file = fileInput.files[0];
            let reader = new FileReader();
            
            reader.onload = async function () {
                let base64Image = reader.result.split(',')[1];
                showToast("Submitting proof to Gemini AI...", "info");
                try {
                    let res = await fetch('/api/tasks/verify-proof', {
                        method: 'POST', 
                        headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify({index: taskIndex, image: base64Image, mime_type: file.type})
                    });
                    let result = await res.json();
                    if(res.ok) { 
                        showToast("AI Verification Complete: " + result.status); 
                        loadDashboardData(); 
                    } else {
                        showToast("Error: " + (result.error || "Verification failed"), "error");
                    }
                } catch(err) {
                    showToast("Network error while submitting proof.", "error");
                }
            };
            reader.onerror = function() {
                showToast("Failed to read selected file.", "error");
            };
            reader.readAsDataURL(file);
        }

        async function markRoutineDone(routineIndex) {
            await fetch('/api/routines/complete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index: routineIndex})});
            showToast("Daily routine marked completed!");
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
        "status": "Pending" if role == "Employee" else "Approved",
        "stance": "CEO" if role == "CEO" else "",
        "permissions": ["assign_tasks", "config_routines", "view_logs"] if role == "CEO" else [],
        "other_permission": "",
    }

    if role == "CEO":
        company_id = str(random.randint(1000000000, 9999999999))
        user_data["company_id"] = company_id
        user_data["company_name"] = data.get("company_name", "MyCorp")
        USERS_DB[email] = user_data
        save_db("users.json", USERS_DB)
        HISTORY_DB.insert(
            0,
            {
                "company_id": company_id,
                "action": f"Company '{user_data['company_name']}' created by CEO {name}",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
        )
        save_db("history.json", HISTORY_DB)
    else:
        company_id = data.get("company_id")
        user_data["company_id"] = company_id
        for u in USERS_DB.values():
            if str(u.get("company_id")) == str(company_id) and u.get("role") == "CEO":
                user_data["company_name"] = u.get("company_name", "Tasko Workspace")
                break
        if "company_name" not in user_data:
            user_data["company_name"] = "Tasko Workspace"
        PENDING_USERS_DB[email] = user_data
        save_db("pending_users.json", PENDING_USERS_DB)

    return jsonify({"success": True, "user": user_data})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    email = data.get("email")
    password = data.get("password")

    user = USERS_DB.get(email) or PENDING_USERS_DB.get(email)
    if not user or user.get("password") != password:
        return jsonify({"error": "Invalid email or password."}), 401
    return jsonify({"success": True, "user": user})


@app.route("/api/check-status", methods=["GET"])
def check_status():
    email = request.args.get("email")
    if email in USERS_DB:
        user = USERS_DB[email]
        return jsonify({
            "status": "Approved", 
            "company_name": user.get("company_name", "Tasko Workspace"),
            "stance": user.get("stance", "Team Member"),
            "permissions": user.get("permissions", []),
            "other_permission": user.get("other_permission", "")
        })
    elif email in PENDING_USERS_DB:
        return jsonify({"status": "Pending"})
    return jsonify({"status": "NotFound"})


@app.route("/api/data", methods=["GET"])
def get_data():
    company_id = request.args.get("company_id")
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")

    today_logs = DAILY_LOGS_DB.get(today_date, {})
    routines_with_status = []
    for idx, r in enumerate(DAILY_ROUTINES_DB):
        if str(r.get("company_id")) == str(company_id):
            r_copy = r.copy()
            r_copy["today_status"] = today_logs.get(str(idx), "Pending")
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
    save_db("tasks.json", TASKS_DB)

    HISTORY_DB.insert(
        0,
        {
            "company_id": company_id,
            "action": f"Task '{title}' assigned to {assigned_to} (Deadline: {deadline or 'None'})",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    save_db("history.json", HISTORY_DB)
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
    save_db("routines.json", DAILY_ROUTINES_DB)

    HISTORY_DB.insert(
        0,
        {
            "company_id": company_id,
            "action": f"Auto-daily routine '{title}' set for {assigned_to}",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    save_db("history.json", HISTORY_DB)
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

    DAILY_LOGS_DB[today_date][str(index)] = "Completed"
    save_db("daily_logs.json", DAILY_LOGS_DB)

    routine = DAILY_ROUTINES_DB[index]
    HISTORY_DB.insert(
        0,
        {
            "company_id": routine.get("company_id"),
            "action": f"Daily routine '{routine['title']}' marked completed for today by {routine['assigned_to']}",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    save_db("history.json", HISTORY_DB)
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
        save_db("tasks.json", TASKS_DB)
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
        save_db("tasks.json", TASKS_DB)

        HISTORY_DB.insert(
            0,
            {
                "company_id": task.get("company_id"),
                "action": f"AI verified task '{task['title']}' for {task['assigned_to']}: {task['status']}",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
        )
        save_db("history.json", HISTORY_DB)
        return jsonify({"success": True, "status": task["status"], "comment": ai_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/enhance-permission", methods=["POST"])
def enhance_permission():
    data = request.json
    prompt_text = data.get("prompt", "")
    if not client:
        return jsonify({"enhanced": prompt_text})
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                f"Rewrite the following custom workplace employee permission or special access requirement into a clear, professional, concise policy statement suitable for corporate access control:\n\n'{prompt_text}'"
            ]
        )
        return jsonify({"enhanced": (response.text or prompt_text).strip()})
    except Exception as e:
        return jsonify({"enhanced": prompt_text})


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
        save_db("messages.json", MESSAGES_DB)
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
    stance = data.get("stance", "Team Member")
    permissions = data.get("permissions", [])
    other_permission = data.get("other_permission", "")

    if email in PENDING_USERS_DB:
        user = PENDING_USERS_DB.pop(email)
        user["status"] = "Approved"
        user["stance"] = stance
        user["permissions"] = permissions
        user["other_permission"] = other_permission
        USERS_DB[email] = user
        save_db("pending_users.json", PENDING_USERS_DB)
        save_db("users.json", USERS_DB)
        
        HISTORY_DB.insert(
            0,
            {
                "company_id": user.get("company_id"),
                "action": f"Employee {user['name']} approved with stance '{stance}'",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
        )
        save_db("history.json", HISTORY_DB)
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
                "stance": u.get("stance"),
                "other_permission": u.get("other_permission", "")
            }
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
        save_db("users.json", USERS_DB)
        HISTORY_DB.insert(
            0,
            {
                "company_id": company_id,
                "action": f"Employee {user.get('name')} was kicked from the workspace",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            },
        )
        save_db("history.json", HISTORY_DB)
        return jsonify({"success": True})
    return jsonify({"error": "Employee not found."}}, 404


@app.route("/api/admin/update-company", methods=["POST"])
def update_company():
    data = request.json
    company_id = data.get("company_id")
    new_name = data.get("company_name")
    for u in USERS_DB.values():
        if str(u.get("company_id")) == str(company_id):
            u["company_name"] = new_name
    save_db("users.json", USERS_DB)
    HISTORY_DB.insert(
        0,
        {
            "company_id": company_id,
            "action": f"Workspace company name updated to '{new_name}'",
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )
    save_db("history.json", HISTORY_DB)
    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)