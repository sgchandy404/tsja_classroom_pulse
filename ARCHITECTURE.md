# Architecture — Classroom Pulse

A technical reference for developers. Covers the data model, permission system, key design decisions, and how the major subsystems fit together.

---

## Table of Contents

- [Stack](#stack)
- [Project Structure](#project-structure)
- [Data Model](#data-model)
- [Permission System (RBAC)](#permission-system-rbac)
- [Academic Year & Term Structure](#academic-year--term-structure)
- [At-Risk Detection](#at-risk-detection)
- [Soft-Delete](#soft-delete)
- [Audit Trail](#audit-trail)
- [Entry Form](#entry-form)
- [UI Conventions](#ui-conventions)
- [Date & Locale Conventions](#date--locale-conventions)
- [Database Migrations](#database-migrations)

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web framework | Flask 3.x |
| ORM | Flask-SQLAlchemy 3.x |
| Auth | Flask-Login + Werkzeug password hashing |
| Database | SQLite (default); configurable via `DATABASE_URL` |
| Templates | Jinja2 |
| CSS | Tailwind CSS (CDN) + Flowbite 2.3 (CDN) |
| JS | Alpine.js 3.x (CDN) |
| Fonts | DM Sans via Google Fonts |

No Node.js. No build step. All frontend dependencies are loaded from CDN.

---

## Project Structure

```
src/
├── app.py              # App factory, schema migration, seed functions, blueprint registration
├── models.py           # All SQLAlchemy models + RANKINGS constants
├── permissions.py      # Permissions class, require_role decorator, log_audit helper
├── auth.py             # /login (GET + POST), /logout
└── routes/
    ├── dashboard.py    # /dashboard/  — grade/week breakdown, week navigation
    ├── entry.py        # /entry/      — weekly entry form + JSON endpoints
    ├── students.py     # /students/<id> — per-student history and term filter
    ├── at_risk.py      # /at-risk/    — at-risk detection list + _active_term_filter()
    ├── settings.py     # /settings/   — academic year + term CRUD
    ├── admin.py        # /admin/      — user, grade, subject, student management
    └── audit.py        # /audit/      — paginated audit trail viewer
```

**App factory** (`create_app()` in `app.py`):
1. Configures Flask from environment variables
2. Initialises SQLAlchemy and Flask-Login
3. Registers all blueprints
4. Within app context: runs `db.create_all()` → `_migrate_schema()` → `_seed_grades()` → seed functions

---

## Data Model

### Constants

```python
RANKINGS      = ["Working Towards", "Meets Expectations", "Exceeds Expectations"]
RANKING_ORDER = {"Working Towards": 1, "Meets Expectations": 2, "Exceeds Expectations": 3}
```

### Models

**`Grade`** — Registry of grade names. A pure string registry; no foreign keys from Student or Subject.
```
id, name (unique), is_active
```

**`Student`** — A student enrolled in a grade.
```
id, name, roll_number, grade (plain string), is_active
```

**`Subject`** — A subject taught in a grade. Unique per `(name, grade)`.
```
id, name, grade (plain string), is_active
```

**`WeeklyEntry`** — One teacher's ranking of one student in one subject for one ISO week. Unique per `(student_id, subject_id, iso_week, iso_year)`.
```
id, student_id → Student, subject_id → Subject
iso_week, iso_year, ranking (one of RANKINGS)
created_by → User, created_at, updated_by → User, updated_at
```

**`User`** — An application user account.
```
id, username (unique), password_hash, active
roles → [UserRole]
```

**`UserRole`** — One role assignment for a user. A user can have multiple rows (multi-role).
```
id, user_id → User
role: "admin" | "coordinator" | "incharge" | "teacher"
grade (nullable), subject_id → Subject (nullable)
```

**`AcademicYear`** — A labelled school year (e.g. "2026–27"). Only one row is active at a time.
```
id, label, start_year (int, April year), is_active
end_year  → property (start_year + 1)
terms     → [Term]
```

**`Term`** — One term within an academic year. Term membership is derived at query time; not stored on WeeklyEntry.
```
id, academic_year_id → AcademicYear, name ("Term 1/2/3")
start_iso_week, start_iso_year, end_iso_week, end_iso_year
is_locked (bool)
contains_week(week, year) → bool
start_date, end_date      → date properties
```

**`AppConfig`** — Key/value store for runtime configuration.
```
key (PK), value
Default: grace_period_hours = "48"
```

**`AuditLog`** — Immutable record of every data mutation.
```
id, user_id → User, timestamp
action: "create" | "edit" | "delete"
model_name, record_id, field_name, old_value, new_value, note
```

### Entity Relationships (simplified)

```
User ──< UserRole >── Subject
                └── Grade (string, not FK)

Student >── WeeklyEntry ──< Subject
AcademicYear ──< Term
WeeklyEntry.iso_week/year ── (derived) ──> Term
```

Grade and Subject store the grade as a plain string (e.g. `"Grade 5A"`). The `Grade` model is a registry for managing which grade strings are active; it does not enforce referential integrity via FK — this keeps the schema simple and avoids cascading issues when grades are deactivated.

---

## Permission System (RBAC)

Defined in `src/permissions.py`. A `Permissions` object is instantiated once per request via the `perms()` factory and injected into all templates via `@app.context_processor`.

### Role matrix

| Role | Entry rights | View rights | Admin rights |
|---|---|---|---|
| Teacher | Assigned grade(s) + subject(s); own entries within grace period | Own grades + subjects only | None |
| In-Charge | Assigned grade(s) + subject(s) | All subjects in assigned grade(s) | Add/edit/deactivate students in own grade(s) |
| Coordinator | Assigned grade(s) + subject(s) | All grades, all subjects | Audit trail (read-only) |
| Admin | Everything, any time | Everything | Full — users, grades, subjects, students, terms, audit |

Multi-role is additive: effective permissions = union of all `UserRole` rows for that user.

### `Permissions` class — key methods

| Method | Returns | Notes |
|---|---|---|
| `is_admin` / `is_coordinator` / `is_incharge` / `is_teacher` | bool | Role presence checks |
| `can_view_all` | bool | True for admin + coordinator |
| `visible_grades()` | None or set[str] | None = unrestricted |
| `can_view_grade(grade)` | bool | |
| `visible_subject_ids_for_grade(grade)` | None or set[int] | In-Charge → None (all subjects in grade) |
| `enterable_pairs()` | None or set[(grade, subject_id)] | None = admin |
| `can_enter(grade, subject_id)` | bool | |
| `enterable_grades()` | None or set[str] | Distinct grades where user can enter |
| `can_manage_students(grade)` | bool | Admin or In-Charge of that grade |
| `manageable_grades()` | None or set[str] | |
| `can_edit_entry(entry)` | bool | Admin: always. Others: own entry + grace period + term not locked |
| `within_grace_period(entry)` | bool | Checks `created_at` vs `AppConfig.grace_period_hours` |

### Route protection

- `@require_role("admin")` decorator on admin-only routes — aborts 403 if role not present
- Per-object checks inside route handlers (e.g. `p.can_view_grade(student.grade)` or 403)
- Template conditionals via `{{ perms.is_admin }}`, `{{ perms.can_view_all }}` etc.

### `log_audit()` helper

Writes one `AuditLog` row. Called by every mutation handler; caller is responsible for `db.session.commit()`.

```python
log_audit(user=current_user, action="edit", model_name="Student",
          record_id=student.id, field_name="is_active",
          old_value="True", new_value="False", note="Deactivated")
```

---

## Academic Year & Term Structure

- School calendar: April–March (India / Cambridge-affiliated)
- Three terms per year: Term 1 (Apr–Sep), Term 2 (Oct–Dec), Term 3 (Jan–Mar)
- Term 3 crosses a calendar year boundary — handled by comparing `(iso_year, iso_week)` tuples in `contains_week()`
- `AcademicYear.is_active` is a single-row flag — `set_active` resets all rows then sets the chosen one
- **Term membership is not stored** — `WeeklyEntry` has no FK to `Term`. Membership is derived at query time via `term.contains_week(e.iso_week, e.iso_year)`. This means historical entries remain valid across year-end transitions without any data migration.
- On first startup, `_seed_default_academic_year()` creates and activates the current year automatically

### `_active_term_filter()` (in `at_risk.py`)

Used by both the at-risk list and the student detail page:
1. Finds the active `AcademicYear`
2. Selects term by `?term_id=` query param, or falls back to the term containing today, or the last term in the year
3. Returns `(academic_year, terms, selected_term)` for the template

---

## At-Risk Detection

`_detect(entries)` in `at_risk.py` — pure Python, no SQL window functions. Takes a list of `WeeklyEntry` objects for one `(student, subject)` pair, sorted oldest → newest, within the selected term.

```
Stuck     — last 3 consecutive weeks all "Working Towards"
Declining — strict downward trend over 3 weeks,
            OR current week = WT with any prior week > WT in the last 3
Improving — current ranking is strictly higher than ALL prior weeks
            (genuine new high; not a return-to-baseline recovery like EE→WT→EE)
```

- Needs ≥ 2 entries for Improving; ≥ 3 for Stuck / Declining
- Reused verbatim in `students.py` for per-subject flags on the student detail page
- Flags reset cleanly at term boundaries because entries are pre-filtered to the selected term before `_detect()` is called

---

## Soft-Delete

`Student.is_active` and `Subject.is_active` are Boolean flags (default `True`).

**Deactivated students:**
- Hidden from entry form, dashboard, at-risk list, search dropdowns
- Still accessible via `/students/<id>` (amber inactive banner shown)
- All `WeeklyEntry` rows are preserved — historical data is never deleted

**Deactivated subjects:**
- Hidden from entry form, dashboard, at-risk subject dropdown
- Historical `WeeklyEntry` rows remain queryable

All active queries apply `.filter(Model.is_active == True)` or `.filter_by(is_active=True)`. The `get_or_404()` PK lookup in `/students/<id>` intentionally does **not** filter by `is_active` — deactivated students are still readable.

---

## Audit Trail

Every data mutation writes to `AuditLog`. Coverage:

| Area | Events logged |
|---|---|
| Weekly entries | create, edit |
| Users | create, password change, activate/deactivate, role changes |
| Grades | add, deactivate, reactivate |
| Subjects | add, deactivate, reactivate |
| Students | add, edit, deactivate, reactivate |
| Academic years | create, delete, set-active |
| Terms | lock, unlock |
| App config | grace period change |

`_describe(log)` in `audit.py` generates a human-readable "What happened" string from each log row, used in the audit table instead of raw field/value columns.

`_parse_date(s)` accepts both `DD/MM/YYYY` and `YYYY-MM-DD` to handle different input methods.

Default filter: current ISO week (Monday → today).

---

## Entry Form

- Grade select → Alpine.js fetches `/entry/students` and `/entry/subjects` (JSON endpoints)
- Subject and student lists are filtered server-side to the user's enterable assignments
- Ranking selects are colour-coded live (red / amber / green) via Alpine `:class` binding
- Form submits a bulk POST: inputs named `ranking_<student_id>_<subject_id>`
- Server-side: upserts in a single transaction; checks permissions, grace period, and term lock per entry; writes audit log entries; flashes a summary of saved / locked / denied counts

---

## UI Conventions

- **Layout** — fixed sidebar (`w-64`, `bg-slate-900`) + scrollable main area
- **Accent colour** — `bg-brand-600` = `#4f46e5` (indigo)
- **Active nav link** — `bg-brand-600 text-white rounded-lg`
- **Ranking badges** — `ranking_badge(ranking)` macro in `macros.html`
  - Working Towards → red pill
  - Meets Expectations → amber pill
  - Exceeds Expectations → green pill
- **Cards** — `bg-white rounded-xl shadow-sm`
- **Page title** — `{% block page_title %}` renders in the topbar `<h1>`
- **Login page** — `auth/login.html` is a fully standalone HTML file; does not extend `base.html`
- **Sidebar visibility** — nav items are conditionally rendered via `{{ perms.* }}` template variables

---

## Date & Locale Conventions

- All dates displayed as `DD/MM/YYYY` (Indian format)
- `<html lang="en-GB">` on `base.html` nudges browser date inputs toward DD/MM/YYYY
- Audit date filters use `type="text"` with `placeholder="DD/MM/YYYY"` to avoid browser locale overrides
- `strftime('%d/%m/%Y')` used throughout all route files

---

## Database Migrations

The app uses a lightweight SQLite-safe migration approach rather than Alembic:

**`_migrate_schema()`** in `app.py` — runs after `db.create_all()` on every startup:
```python
ALTER TABLE students ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1
ALTER TABLE subjects ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1
```
Each statement is wrapped in `try/except` — SQLite raises an error if the column already exists, which is silently ignored. Safe to run on both fresh and existing databases.

**`_seed_grades()`** — if the `Grade` table is empty, scans distinct grade strings from `Student` and `Subject` rows and inserts a `Grade` record for each. Runs once on first startup after a schema migration.

For a production deployment on PostgreSQL or another engine, this pattern should be replaced with Alembic migrations.
