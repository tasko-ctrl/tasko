# Tasko Workspace

Team accountability workspace: assign tasks, run daily routines, chat, and let
Gemini verify photo proof of completion. Built with Flask + SQLite + JWT, with
mandatory e-mail verification (6-digit code via Brevo) and optional Twilio phone verification.

## Features

- Multi-tenant workspaces keyed by a generated Workspace ID
- CEO approval flow with granular permissions per employee
- Tasks with title, instructions, deadline, priority and AI proof verification
- Daily routines that reset at midnight in `APP_TIMEZONE`
- List and Kanban views, filters, search and sorting
- Workspace chat with incremental polling
- Activity log, employee role/permission editing, kick (with re-apply support)
- Sign-up requires a 6-digit code e-mailed through Brevo (no account exists until it is entered)
- Optional SMS phone verification through Twilio Verify
- Downloadable product documentation, linked from the app and landing page

## Product documentation

The Word document lives at `docs/Tasko-Documentation.docx` and is served as a
download at `/documentation`. It is linked from:

- the workspace sidebar and Settings page,
- the command palette (`Ctrl/Cmd+K`),
- the landing page navigation and footer.

Edit the `.docx` in place and redeploy; no code changes are needed.

## Run locally

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env      # then fill in values (Windows)
cp .env.example .env        # macOS / Linux
python Tasko.py
```

Open http://localhost:5000 for the landing page and http://localhost:5000/app
for the workspace. Only `SECRET_KEY`, `DATA_DIR` and (for AI proof checks)
`GEMINI_API_KEY` are needed to start.

AI proof checks try `GEMINI_MODEL` first (comma-separated), then fall back to
current built-in model names, so a retired model never breaks verification.
If a proof check fails, the toast says whether the key or the model needs fixing.

Environment variables are documented in `.env.example`. Do not commit `.env`.

## Deploy on Railway

1. Push the repository and create a Railway service from it.
2. Add a **Volume** mounted at `/data`.
3. Set `DATA_DIR=/data` (the SQLite database, `secret.key` and `proofs/` live there).
4. Set `SECRET_KEY`, `GEMINI_API_KEY`, `BREVO_API_KEY` and `EMAIL_FROM`, etc.
   Without the two Brevo variables nobody can sign up (the app shows a clear message).
5. Health check path: `/healthz`.

`Procfile` runs `gunicorn Tasko:app --workers 2 --threads 4`.

## Data & storage

| Path | Contents |
| --- | --- |
| `$DATA_DIR/tasko.db` | users, tasks, routines, chat, history |
| `$DATA_DIR/secret.key` | auto-generated JWT signing key (if `SECRET_KEY` is unset) |
| `$DATA_DIR/proofs/` | proof images kept for human review |

Back up the whole `DATA_DIR` volume. SQLite runs in WAL mode; copy the `.db`,
`-wal` and `-shm` files together (or stop the service first).

## Main API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/signup` | start a sign-up and e-mail a 6-digit code (no account yet) |
| POST | `/api/email/verify` | check the code, create the account, return the login token |
| POST | `/api/email/resend` | send a new code (60 s cooldown, 5 per hour per address) |
| POST | `/api/login` | email + password + Workspace ID (also resumes an unfinished verification) |
| GET | `/api/data` | tasks, routines, history for the caller |
| POST | `/api/tasks`, `/api/tasks/update`, `/api/tasks/delete` | manage tasks |
| POST | `/api/tasks/verify-proof` | upload an image for AI verification |
| GET | `/api/tasks/proof/<id>` | fetch a stored proof image |
| POST | `/api/tasks/update-status` | manual Kanban override |
| POST | `/api/routines`, `/api/routines/complete`, `/api/routines/delete` | routines |
| GET/POST | `/api/chat` | read (`?after=<id>`) or send messages |
| POST | `/api/admin/approve-user`, `/api/admin/update-employee`, `/api/admin/kick-employee` | people |
| GET/POST | `/api/phone/*` | Twilio Verify flow (admin endpoints require approval) |

## Tests

```bash
pip install pytest
pytest -q
```

## Known limitations / next steps

- Accounts created before e-mail verification existed were not verified. There is no
  password reset yet; it can reuse the same code flow.
- Brevo can only send from an address you verified. Without your own domain, mail from a
  free address (e.g. Gmail) may land in spam; add a custom domain before a public launch.
- SQLite is fine for small teams. The workspace refresh polls `/api/data`; if a
  workspace grows beyond a few dozen active users, move to Postgres and
  WebSockets/SSE for chat.
- The frontend lives in `HTML_TEMPLATE` inside `Tasko.py`. Splitting it into
  `templates/app.html` + `static/` files is the natural next refactor.
- Role changes take effect on the next status poll (about 10 seconds).
