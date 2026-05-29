# Classroom Pulse — CLAUDE.md

## Project
Flask + SQLite student progress tracker for teachers. Weekly rankings per student/subject.
Entry point: `src/app.py` (Flask app factory).

## Stack
- Python 3.11+, Flask 3.x, Flask-SQLAlchemy, Flask-Login, Werkzeug
- SQLite via SQLAlchemy ORM (`src/instance/database.db` — gitignored)
- Jinja2 templates under `src/templates/`
- Tailwind CSS, Flowbite 2.3, Alpine.js 3.x — all via CDN, no build step
- Google Fonts: DM Sans

## Branch workflow
```
feature/<name>  →  dev  →  staging  →  prod
```
- Cut feature branches from `dev`
- Merge feature → `dev` when tested locally
- Merge `dev` → `staging` for QA
- Merge `staging` → `prod` for releases
- Branch protection: `staging` and `prod` — no direct commits

## Running locally
```powershell
pip install -r requirements.txt
cd src
python app.py          # http://127.0.0.1:5000
```
Default credentials: `admin` / `changeme123` (seeded on first run if no users exist).
Demo data: `$env:SEED_DEMO_DATA = "1"; python app.py`

## Project structure
```
src/
  app.py          # factory, init_db, blueprint registration
  models.py       # User, Student, Subject, WeeklyEntry + RANKING_ORDER
  auth.py         # auth blueprint (/login, /logout)
  routes/
    dashboard.py  # /dashboard/
    entry.py      # /entry/
    students.py   # /students/<id>
    at_risk.py    # /at-risk/
  templates/
    base.html     # sidebar shell, topbar, flash messages
    macros.html   # ranking_badge(ranking) macro
    auth/         dashboard/  entry/  students/  at_risk/
```

## Models (src/models.py)
- `RANKING_ORDER` = `{"Working Towards": 1, "Meets Expectations": 2, "Exceeds Expectations": 3}`
- `Student` — name, roll_number, grade (plain string e.g. "Grade 5A")
- `Subject` — name, grade; unique per (name, grade)
- `WeeklyEntry` — student_id, subject_id, iso_week, iso_year, ranking; unique per (student, subject, week, year)
- `User` — username, password_hash (Werkzeug)

## UI conventions
- Sidebar: `bg-slate-900`, accent: `bg-brand-600` (`#4f46e5` indigo)
- Active nav link: `bg-brand-600 text-white rounded-lg`
- Ranking badges: use `ranking_badge(ranking)` macro from `macros.html`
  - Working Towards → red pill
  - Meets Expectations → amber pill
  - Exceeds Expectations → green pill
- Cards: `bg-white rounded-xl shadow-sm p-6`
- Page title set via `{% block page_title %}` (renders in topbar)

## At-risk logic (src/routes/at_risk.py)
Python-side, no SQL window functions. Per (student, subject) group, last 3 entries:
- **Stuck**: all 3 = "Working Towards"
- **Declining**: two consecutive rank drops, or rank 3 → rank 1 in one step

## Phase build plan
| # | Branch | Feature |
|---|---|---|
| 1 | dev | Core skeleton — models, factory, base template ✅ |
| 2 | feature/auth | Login/logout, Flask-Login, login page |
| 3 | feature/entry | Weekly entry form (Alpine.js grade→students/subjects) |
| 4 | feature/dashboard | Grade/week breakdown, ranking distributions |
| 5 | feature/at-risk | At-risk detection list |
| 6 | feature/student-detail | Per-student ranking history |
| 7 | dev | Demo seed data + final polish → staging → prod |
