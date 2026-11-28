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
os.environ.pop("BREVO_API_KEY", None)
os.environ.pop("EMAIL_FROM", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import Tasko  # noqa: E402


@pytest.fixture()
def client():
    Tasko.app.config.update(TESTING=True)
    with Tasko.app.test_client() as test_client:
        yield test_client


# Every test "receives" its verification e-mails here instead of Brevo: SENT[email] = last code.
SENT = {}


@pytest.fixture(autouse=True)
def fake_email(monkeypatch):
    SENT.clear()
    Tasko._rate_hits.clear()  # the sign-up rate limit would otherwise trip after ~15 sign-ups
    monkeypatch.setattr(Tasko, "EMAIL_ENABLED", True)

    def fake_send(email, code):
        SENT[email] = code
        return True

    monkeypatch.setattr(Tasko, "send_verification_email", fake_send)


def start_signup(client, name, email, role="CEO", company_name="Acme", company_id="", password="correct-horse-battery"):
    return client.post("/api/signup", json={
        "name": name,
        "email": email,
        "password": password,
        "role": role,
        "company_name": company_name,
        "company_id": company_id,
    })


def verify_email(client, email, code, token):
    return client.post("/api/email/verify", json={"email": email, "code": code, "verify_token": token})


def signup(client, name, email, role="CEO", company_name="Acme", company_id=""):
    """Full sign-up: fill the form, then type the 6-digit code that was e-mailed."""
    response = start_signup(client, name, email, role, company_name, company_id)
    assert response.status_code == 200, response.get_json()
    started = response.get_json()
    assert started["verify_required"] is True
    verified = verify_email(client, email, SENT[email.lower()], started["verify_token"])
    assert verified.status_code == 200, verified.get_json()
    return verified.get_json()


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


# ---------------------------------------------------------------------------
# E-mail verification
# ---------------------------------------------------------------------------
def _user_exists(email):
    with Tasko.get_db() as conn:
        return conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is not None


def test_signup_creates_no_account_until_the_code_is_entered(client):
    started = start_signup(client, "Zed", "zed@verify.test", company_name="Verify Co").get_json()
    assert started["verify_required"] is True
    assert "token" not in started and "user" not in started
    assert len(SENT["zed@verify.test"]) == 6
    assert not _user_exists("zed@verify.test")

    # Nobody can log in (or use the API) before verifying.
    bad_login = client.post("/api/login", json={
        "email": "zed@verify.test", "password": "nope-nope-nope", "company_id": "1234567890"})
    assert bad_login.status_code == 401

    done = verify_email(client, "zed@verify.test", SENT["zed@verify.test"], started["verify_token"])
    assert done.status_code == 200
    assert done.get_json()["user"]["role"] == "CEO"
    assert _user_exists("zed@verify.test")


def test_codes_are_stored_hashed(client):
    started = start_signup(client, "Hash", "hash@verify.test", company_name="H").get_json()
    code = SENT["hash@verify.test"]
    with Tasko.get_db() as conn:
        row = conn.execute("SELECT * FROM pending_signups WHERE email = ?", ("hash@verify.test",)).fetchone()
    assert code not in row["code_hash"] and code not in row["token_hash"]
    assert started["verify_token"] not in row["token_hash"]
    assert "correct-horse-battery" not in row["password_hash"]


def test_wrong_codes_lock_after_five_tries_and_resend_unlocks(client):
    started = start_signup(client, "Lou", "lou@verify.test", company_name="Lou Co").get_json()
    real = SENT["lou@verify.test"]
    wrong = "000000" if real != "000000" else "111111"
    token = started["verify_token"]

    first = verify_email(client, "lou@verify.test", wrong, token)
    assert first.status_code == 400 and "4 tries left" in first.get_json()["error"]
    for _ in range(4):
        verify_email(client, "lou@verify.test", wrong, token)
    # Even the CORRECT code no longer works once the tries are used up.
    locked = verify_email(client, "lou@verify.test", real, token)
    assert locked.status_code == 429
    assert not _user_exists("lou@verify.test")

    with Tasko.get_db() as conn:  # skip the 60 second wait
        conn.execute("UPDATE pending_signups SET last_sent = last_sent - 120 WHERE email = ?", ("lou@verify.test",))
    resent = client.post("/api/email/resend", json={"email": "lou@verify.test", "verify_token": token})
    assert resent.status_code == 200, resent.get_json()
    assert verify_email(client, "lou@verify.test", SENT["lou@verify.test"], token).status_code == 200


def test_expired_code_is_rejected(client):
    started = start_signup(client, "Exp", "exp@verify.test", company_name="Exp Co").get_json()
    with Tasko.get_db() as conn:
        conn.execute("UPDATE pending_signups SET expires_at = 1 WHERE email = ?", ("exp@verify.test",))
    response = verify_email(client, "exp@verify.test", SENT["exp@verify.test"], started["verify_token"])
    assert response.status_code == 400 and "expired" in response.get_json()["error"]
    assert not _user_exists("exp@verify.test")


def test_someone_else_cannot_hijack_a_pending_signup(client):
    victim = start_signup(client, "Vic", "vic@verify.test", company_name="Vic Co").get_json()
    # An attacker re-submits the form for the victim's address with their own password.
    attacker = start_signup(client, "Evil", "vic@verify.test", company_name="Evil Co", password="attacker-password-1").get_json()
    code = SENT["vic@verify.test"]  # the (new) code goes to the victim's inbox only
    # The victim's old browser token no longer matches -> they are told to start over.
    blocked = verify_email(client, "vic@verify.test", code, victim["verify_token"])
    assert blocked.status_code == 403 and blocked.get_json()["restart"] is True
    assert not _user_exists("vic@verify.test")
    # The attacker has the token but never sees the code.
    guess = verify_email(client, "vic@verify.test", "123456" if code != "123456" else "654321", attacker["verify_token"])
    assert guess.status_code == 400
    assert not _user_exists("vic@verify.test")


def test_resend_has_a_cooldown_and_an_hourly_cap(client):
    started = start_signup(client, "Rex", "rex@verify.test", company_name="Rex Co").get_json()
    token = started["verify_token"]
    too_soon = client.post("/api/email/resend", json={"email": "rex@verify.test", "verify_token": token})
    assert too_soon.status_code == 429 and too_soon.get_json()["retry_after"] > 0

    for _ in range(Tasko.EMAIL_HOURLY_LIMIT - 1):
        with Tasko.get_db() as conn:
            conn.execute("UPDATE pending_signups SET last_sent = last_sent - 120 WHERE email = ?", ("rex@verify.test",))
        assert client.post("/api/email/resend", json={"email": "rex@verify.test", "verify_token": token}).status_code == 200
    with Tasko.get_db() as conn:
        conn.execute("UPDATE pending_signups SET last_sent = last_sent - 120 WHERE email = ?", ("rex@verify.test",))
    capped = client.post("/api/email/resend", json={"email": "rex@verify.test", "verify_token": token})
    assert capped.status_code == 429 and "hour" in capped.get_json()["error"]


def test_failed_email_send_does_not_use_up_the_hourly_limit(client, monkeypatch):
    monkeypatch.setattr(Tasko, "send_verification_email", lambda email, code: False)
    for _ in range(Tasko.EMAIL_HOURLY_LIMIT + 2):
        response = start_signup(client, "Net", "net@verify.test", company_name="Net Co")
        assert response.status_code == 502


def test_logging_in_with_an_unverified_signup_resumes_verification(client):
    ceo = signup(client, "Ann", "ann@verify.test", company_name="Ann Co")
    company_id = str(ceo["user"]["company_id"])
    started = start_signup(client, "Bea", "bea@verify.test", role="Employee", company_id=company_id).get_json()
    first_code = SENT["bea@verify.test"]

    # Tab closed. She logs in again with the password she chose.
    resumed = client.post("/api/login", json={
        "email": "bea@verify.test", "password": "correct-horse-battery", "company_id": company_id})
    assert resumed.status_code == 200
    data = resumed.get_json()
    assert data["verify_required"] is True and "token" not in data
    # Old token stopped working, the new one works with the (still valid) code.
    assert verify_email(client, "bea@verify.test", first_code, started["verify_token"]).status_code == 403
    done = verify_email(client, "bea@verify.test", first_code, data["verify_token"])
    assert done.status_code == 200 and done.get_json()["user"]["status"] == "Pending"
    # A wrong password never resumes anything.
    other = client.post("/api/login", json={
        "email": "bea@verify.test", "password": "totally-wrong-pass", "company_id": company_id})
    assert other.status_code == 401


def test_kicked_user_must_verify_again_before_re_applying(client):
    ceo = signup(client, "Kay", "kay@verify.test", company_name="Kay Co")
    company_id = str(ceo["user"]["company_id"])
    signup(client, "Lee", "lee@verify.test", role="Employee", company_id=company_id)
    assert client.post("/api/admin/kick-employee", headers=auth(ceo["token"]), json={"email": "lee@verify.test"}).status_code == 200

    started = start_signup(client, "Lee", "lee@verify.test", role="Employee", company_id=company_id,
                           password="a-brand-new-password").get_json()
    with Tasko.get_db() as conn:  # nothing changed yet
        assert conn.execute("SELECT status FROM users WHERE email = ?", ("lee@verify.test",)).fetchone()["status"] == "Kicked"
    done = verify_email(client, "lee@verify.test", SENT["lee@verify.test"], started["verify_token"])
    assert done.status_code == 200 and done.get_json()["user"]["status"] == "Pending"


def test_already_registered_email_is_refused(client):
    signup(client, "Dup", "dup@verify.test", company_name="Dup Co")
    assert start_signup(client, "Dup", "dup@verify.test", company_name="Dup 2").status_code == 409


def test_signup_is_refused_when_email_sending_is_not_set_up(client, monkeypatch):
    monkeypatch.setattr(Tasko, "EMAIL_ENABLED", False)
    monkeypatch.setattr(Tasko, "EMAIL_DEV_PRINT_CODES", False)
    response = start_signup(client, "Off", "off@verify.test", company_name="Off Co")
    assert response.status_code == 503
    assert not _user_exists("off@verify.test")


def test_google_and_apple_sign_in_are_gone(client):
    for path in ("/api/auth/google", "/api/auth/apple", "/api/social-login"):
        assert client.post(path, json={}).status_code == 404
    page = client.get("/app").get_data(as_text=True)
    for leftover in ("triggerGoogleSignIn", "triggerAppleSignIn", "Continue with Google", "Continue with Apple",
                     "accounts.google.com", "appleid.apple.com", "GOOGLE_CLIENT_ID", "social-login"):
        assert leftover not in page, leftover
    csp = client.get("/app").headers["Content-Security-Policy"]
    assert "google" not in csp and "apple" not in csp
