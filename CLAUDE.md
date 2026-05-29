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
  app.py              # factory, _migrate_schema, _seed_grades, _seed_admin, _seed_default_academic_year, _seed_demo, blueprint registration
  models.py           # all models + RANKING_ORDER + RANKINGS (see Models section)
  permissions.py      # Permissions class, require_role decorator, log_audit helper
  auth.py             # auth blueprint (/login GET+POST, /logout)
  routes/
    dashboard.py      # /dashboard/ — grade/week filter, prev+next week, month+year jump
    entry.py          # /entry/ GET+POST, /entry/students, /entry/subjects (JSON)
    students.py       # /students/<id> — per-student history, term filter, reuses _detect()
    at_risk.py        # /at-risk/ — grade + subject + term filter, stuck/declining/improving detection
    settings.py       # /settings/ — academic year + term CRUD
    admin.py          # /admin/ — user, grade, subject, student management + app config
    audit.py          # /audit/ — paginated audit trail viewer
  templates/
    base.html         # sidebar (logo→/, Menu + Admin sections), topbar, flash messages
    macros.html       # ranking_badge(ranking) macro
    errors/403.html   # standalone 403 page
    errors/404.html   # standalone 404 page
    auth/login.html   # standalone dark login page (no extends)
    dashboard/index.html
    entry/weekly_form.html
    students/detail.html
    at_risk/list.html
    settings/index.html
    settings/new_year.html
    settings/edit_year.html
    admin/index.html              # user list
    admin/new_user.html
    admin/edit_user.html
    admin/_role_form.html         # shared role-assignment checkboxes partial
    admin/config.html             # grace period setting
    admin/grades.html             # grade list + inline add form
    admin/deactivate_grade_confirm.html
    admin/subjects.html           # subject list with grade filter
    admin/new_subject.html
    admin/deactivate_subject_confirm.html
    admin/students.html           # student list scoped by role
    admin/new_student.html
    admin/edit_student.html
    admin/deactivate_student_confirm.html
    audit/index.html              # audit log table
```

## Models (`src/models.py`)
- `RANKINGS` = ordered list `["Working Towards", "Meets Expectations", "Exceeds Expectations"]`
- `RANKING_ORDER` = `{"Working Towards": 1, "Meets Expectations": 2, "Exceeds Expectations": 3}`
- `Grade` — name (unique string registry, e.g. "Grade 5A"), is_active
- `Student` — name, roll_number, grade (plain string), is_active
- `Subject` — name, grade, is_active; unique per (name, grade)
- `WeeklyEntry` — student_id, subject_id, iso_week, iso_year, ranking, created_by, created_at, updated_by, updated_at; unique per (student, subject, week, year)
- `User` — username, password_hash (Werkzeug), active; has `roles` → `UserRole`
- `UserRole` — user_id, role ("admin"|"coordinator"|"incharge"|"teacher"), grade (nullable), subject_id (nullable)
- `AcademicYear` — label ("2026–27"), start_year (April year, int), is_active (bool); `end_year` property
- `Term` — academic_year_id, name ("Term 1/2/3"), start/end ISO week+year, is_locked; `contains_week(week, year)` helper; `start_date`/`end_date` properties
- `AppConfig` — key/value store; key "grace_period_hours" (default "48")
- `AuditLog` — user_id, timestamp, action ("create"|"edit"|"delete"), model_name, record_id, field_name, old_value, new_value, note

## Academic year & term structure
- India / Cambridge-affiliated calendar: April–March, three terms
  - Term 1: April–September | Term 2: October–December | Term 3: January–March
- On first startup, `_seed_default_academic_year()` creates and activates the current academic year with default boundaries — no manual setup required
- `AcademicYear.is_active` is a single-row flag; `set_active` action in settings resets all rows then sets the chosen one
- Term membership is **derived** (no FK on WeeklyEntry) — `term.contains_week(w, y)` compares `(year, week)` tuples; correctly handles year-boundary terms (Term 3 spans Jan–Mar of the next calendar year)
- Settings page (`/settings`): list years → create new (auto-fills defaults) → edit term boundaries via date pickers → set active / delete

## At-risk & student detail — term scoping
- `_active_term_filter()` in `at_risk.py` — resolves active AcademicYear + its Terms, selects term by `?term_id=` param or falls back to the term containing today
- At-risk and student detail both pre-filter entries to the selected term before running `_detect()`; flags reset cleanly across terms
- Term selector dropdown shown in header of both pages (Student Watch + Student Detail)

## RBAC (`src/permissions.py`)

### Roles
| Role | Entry | View | Admin |
|---|---|---|---|
| Teacher | Assigned grade(s) + subject(s); own entries within grace period | Own grade(s) + subject(s) only | None |
| In-Charge | Assigned grade(s) + subject(s) | All subjects in assigned grade(s) | Add/edit/deactivate students in own grade(s) |
| Coordinator | Assigned grade(s) + subject(s) | All grades, all subjects | Audit trail read-only |
| Admin | Everything, any time | Everything | Full: users, grades, subjects, students, terms, audit |

Multi-role: effective permissions = union of all assigned roles.

### `Permissions` class (instantiated per request via `perms()` factory)
- `is_admin`, `is_coordinator`, `is_incharge`, `is_teacher` — role presence checks
- `can_view_all` — True for admin + coordinator
- `visible_grades()` — None (unrestricted) or set of grade strings
- `can_view_grade(grade)` — True if admin/coordinator or grade in visible_grades()
- `visible_subject_ids_for_grade(grade)` — None or set of subject_ids; In-Charge sees all subjects in their grade
- `enterable_pairs()` — None (admin) or set of (grade, subject_id)
- `can_enter(grade, subject_id)` — entry permission check
- `enterable_grades()` — distinct grades where user can enter
- `can_manage_students(grade)` — True for admin or incharge of that grade
- `manageable_grades()` — None (admin) or set of grades where user is In-Charge
- `can_edit_entry(entry)` — admin always; others: own entry + within grace period + term not locked
- `within_grace_period(entry)` — checks `created_at` against `AppConfig grace_period_hours`

### `require_role(*roles)` decorator — aborts 403 if user lacks all listed roles
### `log_audit(...)` helper — writes one `AuditLog` row; caller must commit

### Injected into templates via `@app.context_processor`:
`{{ perms.is_admin }}`, `{{ perms.can_view_all }}`, `{{ perms.is_incharge }}` etc.

## Soft-delete
- `Student.is_active` and `Subject.is_active` — Boolean flags, default True
- All active queries filter `.filter(Model.is_active == True)` or `.filter_by(is_active=True)`
- Deactivated students: hidden from entry/dashboard/at-risk; still accessible via `/students/<id>` (amber banner shown)
- Historical `WeeklyEntry` rows are **never deleted**
- `_migrate_schema()` in `app.py`: SQLite-safe `ALTER TABLE ADD COLUMN` for is_active on existing DBs
- `_seed_grades()` in `app.py`: auto-populates `Grade` table from distinct Student/Subject strings on first run

## Grade registry
- `Grade` model is a pure string registry — no FK relationships to Student/Subject
- `_all_grades()` in `admin.py` reads `Grade.query.filter_by(is_active=True)` first; falls back to union-of-strings from Student/Subject if table is empty
- Deactivating a grade hides it from entry forms and grade selectors; historical data unaffected

## Audit trail (`src/routes/audit.py`)
- All mutations (WeeklyEntry create/edit, user create/edit/deactivate, role changes, grade/subject/student add/deactivate/reactivate, term lock/unlock, academic year create/delete/set-active, grace period change) write to `AuditLog`
- `_describe(log)` — generates human-readable "What happened" string per model/action/field
- `_parse_date(s)` — accepts DD/MM/YYYY or YYYY-MM-DD for filter inputs
- Default `date_from` = Monday of the current ISO week
- Paginated, 50 rows per page; filterable by user, date range, action

## Date / locale conventions
- All dates displayed in DD/MM/YYYY (Indian format)
- `<html lang="en-GB">` on base.html forces browser date inputs to DD/MM/YYYY
- Audit date filters use `type="text"` with `placeholder="DD/MM/YYYY"` (avoids browser locale issues)
- `strftime('%d/%m/%Y')` used throughout all route files

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
Python-side, no SQL window functions. `_detect(entries)` takes entries per (student, subject) **within the selected term**:
- **Stuck**: last 3 all "Working Towards"
- **Declining**: strict 3-entry downward trend, or current = WT with any prior > WT in last 3
- **Improving**: current ranking is strictly higher than ALL prior weeks (genuine new high, not recovery)
- Reused in `students.py` for per-subject flags on the detail page
- Filters: term dropdown + grade dropdown + subject dropdown; all auto-submit on change
- Subject dropdown scoped by role: Teacher sees only their assigned subjects; In-Charge/Coordinator/Admin see all
- Column order: Student → Grade → Subject → Current Ranking → Last 3 Weeks → Flag
- Entry form week selector shows friendly date-range labels ("26/05/2026 – 01/06/2026"); week number hidden from UI

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
| 9 | feature/improving-flag | Improving flag, unified table, subject dedup, subtle highlights, search UX, friendly week labels ✅ |
| 10 | feature/academic-year | AcademicYear + Term models, Settings page, term-scoped at-risk + student detail ✅ |
| 11 | feature/rbac | RBAC: four roles, Permissions class, require_role decorator, grace period, term locking, audit trail, user management UI ✅ |
| 12 | feature/grade-subject-student-mgmt | Grade registry, Subject management, Student management, soft-delete (is_active), role-scoped views, DD/MM/YYYY dates, audit trail completeness ✅ |
