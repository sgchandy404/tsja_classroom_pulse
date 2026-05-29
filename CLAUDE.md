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
- Merge feature → `dev` when tested locally — **confirm with user before merging**
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
Fresh DB: delete `src/instance/database.db` then re-run with the env var.

## Project structure
```
src/
  app.py              # factory, init_db, _seed_admin, _seed_demo, blueprint registration
  models.py           # User, Student, Subject, WeeklyEntry + RANKING_ORDER + RANKINGS
  auth.py             # auth blueprint (/login GET+POST, /logout)
  routes/
    dashboard.py      # /dashboard/ — grade/week filter, prev+next week, month+year jump
    entry.py          # /entry/ GET+POST, /entry/students, /entry/subjects (JSON)
    students.py       # /students/<id> — per-student history, reuses _detect()
    at_risk.py        # /at-risk/ — grade + subject filter, stuck/declining detection
  templates/
    base.html         # sidebar (logo links to /), topbar, flash messages
    macros.html       # ranking_badge(ranking) macro
    errors/404.html   # standalone 404 page
    auth/login.html   # standalone dark login page (no extends)
    dashboard/index.html
    entry/weekly_form.html
    students/detail.html
    at_risk/list.html
```

## Models (`src/models.py`)
- `RANKINGS` = ordered list `["Working Towards", "Meets Expectations", "Exceeds Expectations"]`
- `RANKING_ORDER` = `{"Working Towards": 1, "Meets Expectations": 2, "Exceeds Expectations": 3}`
- `Student` — name, roll_number, grade (plain string e.g. "Grade 5A")
- `Subject` — name, grade; unique per (name, grade)
- `WeeklyEntry` — student_id, subject_id, iso_week, iso_year, ranking; unique per (student, subject, week, year)
- `User` — username, password_hash (Werkzeug)

## UI conventions
- Sidebar: `bg-slate-900`, accent: `bg-brand-600` (`#4f46e5` indigo)
- Logo in sidebar is a link to `/` (index → redirects to dashboard)
- Active nav link: `bg-brand-600 text-white rounded-lg`
- Ranking badges: use `ranking_badge(ranking)` macro from `macros.html`
  - Working Towards → red pill
  - Meets Expectations → amber pill
  - Exceeds Expectations → green pill
- Cards: `bg-white rounded-xl shadow-sm p-6`
- Page title set via `{% block page_title %}` (renders in topbar)
- `auth/login.html` is a fully standalone HTML file — does NOT extend `base.html`

## Dashboard week navigation
- Topbar has month + year selects (auto-submit on change) for quick jumps
- Arrows (← →) step one week at a time
- Month/year jump computes ISO week of the 1st of the selected month via `_adjacent_week()`
- Year range: 2023 → current year + 1

## At-risk logic (`src/routes/at_risk.py`)
Python-side, no SQL window functions. `_detect(entries)` takes last 3 entries per (student, subject):
- **Stuck**: all 3 = "Working Towards"
- **Declining**: two consecutive rank drops, or rank 3 → rank 1 in one step
- Reused in `students.py` for per-subject flags on the detail page
- Filters: grade dropdown + subject dropdown (subject list narrows by grade); Clear link resets both
- Column order: Student → Grade → Subject → Current Ranking → Last 3 Weeks → Flag

## Entry form (`src/routes/entry.py`)
- Alpine.js `x-data` block; grade select fetches `/entry/students` and `/entry/subjects` (JSON)
- Ranking selects colour-coded live (red/amber/green) via `:class` binding
- Bulk POST: inputs named `ranking_<student_id>_<subject_id>`; upserts in one transaction

## Completed phases
| # | Branch | Feature |
|---|---|---|
| 1 | dev | Core skeleton — models, factory, base template ✅ |
| 2 | feature/auth | Login/logout, Flask-Login, standalone login page ✅ |
| 3 | feature/entry | Weekly entry form, Alpine.js grade→students/subjects, login fix ✅ |
| 4 | feature/dashboard | Grade/week breakdown, ranking distributions ✅ |
| 5 | feature/at-risk | At-risk detection list ✅ |
| 6 | feature/student-detail | Per-student ranking history ✅ |
| 7 | feature/seed-polish | Demo seed data, 404 page ✅ |
| 8 | feature/ux-tweaks | Logo link, week prev/next + month/year jump, at-risk subject filter, column swap ✅ |
