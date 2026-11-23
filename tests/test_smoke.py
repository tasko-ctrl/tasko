"""Smoke tests for the core Tasko flows.

They run against a throwaway SQLite database in a temp folder, so the real
``tasko_data`` folder is never touched.
"""

import os
import sys
import tempfile

DATA_DIR = tempfile.mkdtemp(prefix="tasko_test_")
os.environ["DATA_DIR"] = DATA_DIR
os.environ["SECRET_KEY"] = "test-secret-key-0123456789"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("TWILIO_ACCOUNT_SID", None)
os.environ.pop("TWILIO_AUTH_TOKEN", None)
os.environ.pop("TWILIO_VERIFY_SERVICE_SID", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import Tasko  # noqa: E402


@pytest.fixture()
def client():
    Tasko.app.config.update(TESTING=True)
    with Tasko.app.test_client() as test_client:
        yield test_client


def signup(client, name, email, role="CEO", company_name="Acme", company_id=""):
    response = client.post("/api/signup", json={
        "name": name,
        "email": email,
        "password": "correct-horse-battery",
        "role": role,
        "company_name": company_name,
        "company_id": company_id,
    })
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_landing_and_app_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/app").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


def test_ceo_can_create_workspace_and_task(client):
    data = signup(client, "Alice", "alice@acme.test")
    assert data["user"]["role"] == "CEO"
    assert data["user"]["status"] == "Approved"
    assert len(str(data["user"]["company_id"])) == 10

    token = data["token"]
    created = client.post("/api/tasks", headers=auth(token), json={
        "title": "Open the store",
        "description": "Photograph the open shutter and the till.",
        "assigned_to": "Alice",
        "deadline": "2026-12-01T09:00",
        "priority": "High",
    })
    assert created.status_code == 200, created.get_json()

    dashboard = client.get("/api/data", headers=auth(token)).get_json()
    assert len(dashboard["tasks"]) == 1
    task = dashboard["tasks"][0]
    assert task["title"] == "Open the store"
    assert task["description"].startswith("Photograph")
    assert task["status"] == "Pending"

    edited = client.post("/api/tasks/update", headers=auth(token), json={
        "task_id": task["id"],
        "title": "Open the store (edited)",
        "description": "Updated instructions",
        "assigned_to": "Alice",
        "deadline": "",
        "priority": "Medium",
    })
    assert edited.status_code == 200

    moved = client.post("/api/tasks/update-status", headers=auth(token), json={
        "task_id": task["id"], "status": "Completed",
    })
    assert moved.status_code == 200

    deleted = client.post("/api/tasks/delete", headers=auth(token), json={"task_id": task["id"]})
    assert deleted.status_code == 200
    assert client.get("/api/data", headers=auth(token)).get_json()["tasks"] == []


def test_pending_employee_is_blocked_until_approved(client):
    ceo = signup(client, "Bob", "bob@globex.test", company_name="Globex")
    company_id = str(ceo["user"]["company_id"])

    employee = signup(client, "Eve", "eve@globex.test", role="Employee", company_id=company_id)
    assert employee["user"]["status"] == "Pending"
    assert client.get("/api/data", headers=auth(employee["token"])).status_code == 403

    pending = client.get("/api/admin/pending-users", headers=auth(ceo["token"])).get_json()
    assert [u["email"] for u in pending["pending_users"]] == ["eve@globex.test"]

    approved = client.post("/api/admin/approve-user", headers=auth(ceo["token"]), json={
        "email": "eve@globex.test",
        "stance": "Operations",
        "permissions": ["view_logs"],
    })
    assert approved.status_code == 200
    assert client.get("/api/data", headers=auth(employee["token"])).status_code == 200


def test_tenant_isolation_and_login_rules(client):
    first = signup(client, "Cara", "cara@one.test", company_name="One")
    second = signup(client, "Dan", "dan@two.test", company_name="Two")

    task = client.post("/api/tasks", headers=auth(first["token"]), json={
        "title": "Secret task", "assigned_to": "Cara",
    }).get_json()

    with Tasko.get_db() as conn:
        task_id = conn.execute("SELECT id FROM tasks WHERE title = 'Secret task'").fetchone()["id"]

    assert client.get("/api/data", headers=auth(second["token"])).get_json()["tasks"] == []
    assert client.post("/api/tasks/delete", headers=auth(second["token"]),
                       json={"task_id": task_id}).status_code == 404

    wrong_workspace = client.post("/api/login", json={
        "email": "cara@one.test",
        "password": "correct-horse-battery",
        "company_id": str(second["user"]["company_id"]),
    })
    assert wrong_workspace.status_code == 401

    right_workspace = client.post("/api/login", json={
        "email": "cara@one.test",
        "password": "correct-horse-battery",
        "company_id": str(first["user"]["company_id"]),
    })
    assert right_workspace.status_code == 200


def test_role_editing_permissions(client):
    ceo = signup(client, "Fay", "fay@roles.test", company_name="Roles")
    company_id = str(ceo["user"]["company_id"])

    signup(client, "Gus", "gus@roles.test", role="Employee", company_id=company_id)
    client.post("/api/admin/approve-user", headers=auth(ceo["token"]), json={
        "email": "gus@roles.test",
        "stance": "Team Member",
        "permissions": ["assign_tasks"],
    })

    updated = client.post("/api/admin/update-employee", headers=auth(ceo["token"]), json={
        "email": "gus@roles.test",
        "stance": "Shift Lead",
        "permissions": ["assign_tasks", "view_logs"],
    })
    assert updated.status_code == 200

    employees = client.get("/api/admin/employees-list", headers=auth(ceo["token"])).get_json()["employees"]
    gus = next(e for e in employees if e["email"] == "gus@roles.test")
    assert gus["stance"] == "Shift Lead"
    assert set(gus["permissions"]) == {"assign_tasks", "view_logs"}

    kicked = client.post("/api/admin/kick-employee", headers=auth(ceo["token"]),
                         json={"email": "gus@roles.test"})
    assert kicked.status_code == 200
    login = client.post("/api/login", json={
        "email": "gus@roles.test",
        "password": "correct-horse-battery",
        "company_id": company_id,
    })
    assert login.status_code == 403


def test_proof_endpoint_requires_a_stored_image(client):
    ceo = signup(client, "Hal", "hal@proof.test", company_name="Proof")
    task = client.post("/api/tasks", headers=auth(ceo["token"]), json={
        "title": "Clean desk", "assigned_to": "Hal",
    })
    assert task.status_code == 200
    with Tasko.get_db() as conn:
        task_id = conn.execute("SELECT id FROM tasks WHERE title = 'Clean desk'").fetchone()["id"]

    response = client.get(f"/api/tasks/proof/{task_id}", headers=auth(ceo["token"]))
    assert response.status_code == 404

    # No Gemini key is configured in tests, so proof submission must fail cleanly.
    response = client.post("/api/tasks/verify-proof", headers=auth(ceo["token"]), json={
        "task_id": task_id, "image": "aGVsbG8=",
    })
    assert response.status_code == 400


def test_routines_complete_and_delete(client):
    ceo = signup(client, "Ivy", "ivy@routines.test", company_name="Routines")
    created = client.post("/api/routines", headers=auth(ceo["token"]), json={
        "title": "Morning check", "assigned_to": "Ivy",
    })
    assert created.status_code == 200

    routines = client.get("/api/data", headers=auth(ceo["token"])).get_json()["routines"]
    assert len(routines) == 1
    routine = routines[0]
    assert routine["today_status"] == "Pending"

    done = client.post("/api/routines/complete", headers=auth(ceo["token"]),
                       json={"routine_id": routine["id"]})
    assert done.status_code == 200
    routines = client.get("/api/data", headers=auth(ceo["token"])).get_json()["routines"]
    assert routines[0]["today_status"] == "Completed"

    deleted = client.post("/api/routines/delete", headers=auth(ceo["token"]),
                          json={"routine_id": routine["id"]})
    assert deleted.status_code == 200
    assert client.get("/api/data", headers=auth(ceo["token"])).get_json()["routines"] == []


def test_chat_incremental_polling(client):
    ceo = signup(client, "Jo", "jo@chat.test", company_name="Chat")
    headers = auth(ceo["token"])

    assert client.get("/api/chat", headers=headers).get_json()["messages"] == []
    assert client.post("/api/chat", headers=headers, json={"text": "hello"}).status_code == 200
    assert client.post("/api/chat", headers=headers, json={"text": "again"}).status_code == 200

    first = client.get("/api/chat", headers=headers).get_json()
    assert [m["text"] for m in first["messages"]] == ["hello", "again"]
    assert first["last_id"] == first["messages"][-1]["id"]

    newer = client.get(f"/api/chat?after={first['last_id']}", headers=headers).get_json()
    assert newer["messages"] == []

    client.post("/api/chat", headers=headers, json={"text": "third"})
    newer = client.get(f"/api/chat?after={first['last_id']}", headers=headers).get_json()
    assert [m["text"] for m in newer["messages"]] == ["third"]


def test_proof_is_verified_and_stored(client, monkeypatch):
    import base64
    import io

    from PIL import Image

    class FakeParsed:
        def model_dump(self):
            return {"passed": True, "reason": "The photo clearly shows the completed work."}

    class FakeResponse:
        parsed = FakeParsed()

    class FakeModels:
        def generate_content(self, **_kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(Tasko, "client", FakeClient())

    ceo = signup(client, "Kim", "kim@aiproof.test", company_name="AI Proof")
    client.post("/api/tasks", headers=auth(ceo["token"]), json={
        "title": "Water the plants",
        "description": "Photo of the watered plants",
        "assigned_to": "Kim",
    })
    with Tasko.get_db() as conn:
        task_id = conn.execute("SELECT id FROM tasks WHERE title = 'Water the plants'").fetchone()["id"]

    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(buffer, format="PNG")
    image = base64.b64encode(buffer.getvalue()).decode()

    result = client.post("/api/tasks/verify-proof", headers=auth(ceo["token"]), json={
        "task_id": task_id, "image": image, "mime_type": "image/png",
    })
    assert result.status_code == 200, result.get_json()
    assert result.get_json()["status"] == "Completed"

    tasks = client.get("/api/data", headers=auth(ceo["token"])).get_json()["tasks"]
    task = next(t for t in tasks if t["id"] == task_id)
    assert task["status"] == "Completed"
    assert task["proof_path"]

    proof = client.get(f"/api/tasks/proof/{task_id}", headers=auth(ceo["token"]))
    assert proof.status_code == 200
    assert proof.mimetype == "image/png"

    # A completed task cannot be submitted again.
    again = client.post("/api/tasks/verify-proof", headers=auth(ceo["token"]), json={
        "task_id": task_id, "image": image,
    })
    assert again.status_code == 400

    assert client.post("/api/tasks/delete", headers=auth(ceo["token"]),
                       json={"task_id": task_id}).status_code == 200
    assert not os.path.exists(os.path.join(Tasko.PROOFS_DIR, task["proof_path"]))


def test_csp_allows_inline_app_scripts(client):
    response = client.get("/app")
    csp = response.headers.get("Content-Security-Policy", "")
    # The whole UI is inline JS + inline onclick handlers, so blocking inline
    # scripts silently breaks every button (including the login form).
    assert "script-src" in csp
    assert "'unsafe-inline'" in csp


def test_documentation_download(client):
    response = client.get("/documentation")
    assert response.status_code == 200
    assert response.mimetype == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in response.headers.get("Content-Disposition", "")


def _employee_task(client, suffix):
    """Create a workspace with one approved employee and one task assigned to them."""
    import base64
    import io

    from PIL import Image

    ceo = signup(client, f"Boss{suffix}", f"boss{suffix}@aiemp.test", company_name=f"AI Emp {suffix}")
    company_id = str(ceo["user"]["company_id"])
    employee = signup(client, f"Worker{suffix}", f"worker{suffix}@aiemp.test", role="Employee",
                      company_name="", company_id=company_id)
    client.post("/api/admin/approve-user", headers=auth(ceo["token"]), json={
        "email": f"worker{suffix}@aiemp.test", "stance": "Ops", "permissions": [],
    })
    client.post("/api/tasks", headers=auth(ceo["token"]), json={
        "title": "Clean the desk",
        "description": "Photo of the clean desk",
        "assigned_to": f"Worker{suffix}",
        "priority": "High",
    })
    tasks = client.get("/api/data", headers=auth(employee["token"])).get_json()["tasks"]
    assert len(tasks) == 1
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="JPEG")
    image = base64.b64encode(buffer.getvalue()).decode()
    return employee, tasks[0]["id"], image


def _fake_gemini(fail_models=(), error="404 NOT_FOUND: model not found", calls=None):
    class FakeParsed:
        def model_dump(self):
            return {"passed": True, "reason": "The photo clearly shows the clean desk."}

    class FakeResponse:
        parsed = FakeParsed()

    class FakeModels:
        def generate_content(self, **kwargs):
            model = kwargs.get("model")
            if calls is not None:
                calls.append(model)
            if model in fail_models:
                raise RuntimeError(error)
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    return FakeClient()


def test_gemini_models_keep_live_fallbacks():
    # The default list used to be gemini-2.5-flash,gemini-2.0-flash. Those are retired,
    # which silently broke AI verification for every user.
    assert len(Tasko.GEMINI_MODELS) == len(set(Tasko.GEMINI_MODELS))
    assert "gemini-3.5-flash" in Tasko.GEMINI_MODELS


def test_employee_proof_falls_back_to_a_current_model(client, monkeypatch):
    calls = []
    monkeypatch.setattr(Tasko, "GEMINI_MODELS", ["gemini-2.0-flash", "gemini-3.5-flash"])
    monkeypatch.setattr(Tasko, "client", _fake_gemini(fail_models={"gemini-2.0-flash"}, calls=calls))

    employee, task_id, image = _employee_task(client, "A")
    response = client.post("/api/tasks/verify-proof", headers=auth(employee["token"]), json={
        "task_id": task_id, "image": image, "mime_type": "image/jpeg",
    })
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["status"] == "Completed"
    assert calls == ["gemini-2.0-flash", "gemini-3.5-flash"]


def test_retired_models_return_a_clear_error(client, monkeypatch):
    monkeypatch.setattr(Tasko, "GEMINI_MODELS", ["gemini-2.5-flash", "gemini-2.0-flash"])
    monkeypatch.setattr(Tasko, "client", _fake_gemini(fail_models={"gemini-2.5-flash", "gemini-2.0-flash"}))

    employee, task_id, image = _employee_task(client, "B")
    response = client.post("/api/tasks/verify-proof", headers=auth(employee["token"]), json={
        "task_id": task_id, "image": image, "mime_type": "image/jpeg",
    })
    assert response.status_code == 503
    assert "GEMINI_MODEL" in response.get_json()["error"]
