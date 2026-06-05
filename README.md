# Classroom Pulse

A lightweight web application for schools to track and monitor student progress week by week. Teachers record performance rankings per student and subject; coordinators and admins get a real-time view of at-risk students, grade-level trends, and a full audit trail.

Built with Flask, SQLite, and Alpine.js — no build step required.

---

## Features

- **Weekly entry form** — Teachers select a grade, week, and record a ranking (Working Towards / Meets Expectations / Exceeds Expectations) for each student × subject combination
- **Dashboard** — Grade-level breakdown of ranking distributions for any selected week; navigate by week or jump to a month
- **Student Watch (At-Risk)** — Automatically flags students as Stuck, Declining, or Improving based on rolling weekly trends within the current term
- **Student detail** — Full ranking history per student across all subjects, with per-subject trend indicators
- **Academic year & term management** — Supports India/Cambridge-style April–March calendars with three terms; term-scoped reporting resets cleanly each term
- **Role-based access control** — Four roles with distinct entry, view, and admin rights (see [Roles](#roles))
- **Grade, subject & student management** — Admins manage the full registry; In-Charges manage students within their own grade
- **Soft-delete** — Students and subjects are deactivated, never deleted; all historical ranking data is preserved
- **Audit trail** — Every data mutation is logged with user, timestamp, and before/after values
- **Grace period & term locking** — Teachers can self-correct entries within a configurable window; admins can override at any time

---

## Roles

| Role | Entry | View | Admin |
|---|---|---|---|
| **Teacher** | Assigned grade(s) + subject(s); own entries within grace period | Own grade(s) + subject(s) only | — |
| **In-Charge** | Assigned grade(s) + subject(s) | All subjects in assigned grade(s) | Add / edit / deactivate students in own grade(s) |
| **Coordinator** | Assigned grade(s) + subject(s) | All grades, all subjects | Audit trail (read-only) |
| **Admin** | Everything, any time | Everything | Users, grades, subjects, students, terms, audit |

Multi-role is supported — effective permissions are the union of all assigned roles.

---

## Stack

- **Backend** — Python 3.11+, Flask 3.x, Flask-SQLAlchemy, Flask-Login, Werkzeug
- **Database** — SQLite (default); any SQLAlchemy-compatible database via `DATABASE_URL`
- **Frontend** — Jinja2 templates, Tailwind CSS (CDN), Flowbite 2.3 (CDN), Alpine.js 3.x (CDN)
- **Fonts** — DM Sans via Google Fonts

No Node.js, no build step.

---

## Getting Started

### Prerequisites

- Python 3.11 or later
- pip

### Installation

```bash
git clone https://github.com/sgchandy404/tsja_classroom_pulse.git
cd tsja_classroom_pulse

pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

At minimum, set a strong `SECRET_KEY`:

```bash
# Generate a secure key
python -c "import secrets; print(secrets.token_hex(32))"
```

See [`.env.example`](.env.example) for all available options.

### Run

```bash
cd src
python app.py
```

The app starts at **http://127.0.0.1:5000**.

On first run, a default admin account is created:

| Username | Password |
|---|---|
| `admin` | `changeme123` (or your `ADMIN_PASSWORD` env var) |

**Change the admin password immediately after first login.**

### Load demo data (optional)

To seed the database with sample grades, students, subjects, and six weeks of ranking entries:

```bash
# Windows (PowerShell)
$env:SEED_DEMO_DATA = "1"; python app.py

# macOS / Linux
SEED_DEMO_DATA=1 python app.py
```

Demo accounts created (password: `test123`):

| Username | Role |
|---|---|
| `teacher_5a` | Teacher — Grade 5A, Mathematics + Science |
| `incharge_5a` | In-Charge — Grade 5A |
| `coordinator` | Coordinator (all grades, read-only) |

### Fresh database

Delete `src/instance/database.db` and restart the app (with or without `SEED_DEMO_DATA`).

---

## Project Structure

```
tsja_classroom_pulse/
├── .env.example          # Environment variable template
├── requirements.txt
└── src/
    ├── app.py            # App factory, schema migration, seed functions
    ├── models.py         # SQLAlchemy models
    ├── permissions.py    # Permissions class, require_role decorator, log_audit helper
    ├── auth.py           # /login, /logout
    ├── routes/
    │   ├── dashboard.py  # /dashboard/
    │   ├── entry.py      # /entry/
    │   ├── students.py   # /students/<id>
    │   ├── at_risk.py    # /at-risk/
    │   ├── settings.py   # /settings/
    │   ├── admin.py      # /admin/
    │   └── audit.py      # /audit/
    ├── templates/
    │   ├── base.html
    │   ├── macros.html
    │   ├── auth/
    │   ├── dashboard/
    │   ├── entry/
    │   ├── students/
    │   ├── at_risk/
    │   ├── settings/
    │   ├── admin/
    │   ├── audit/
    │   └── errors/
    └── instance/
        └── database.db   # SQLite database (gitignored)
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(insecure fallback)* | Flask session signing key. **Required in production.** |
| `DATABASE_URL` | `sqlite:///database.db` | SQLAlchemy database URI |
| `ADMIN_PASSWORD` | `changeme123` | Password for the seeded admin account (first run only) |
| `SEED_DEMO_DATA` | unset | Set to `1` to load demo data on first run |

---

## Academic Year & Term Structure

The app follows an April–March school year with three terms:

| Term | Period |
|---|---|
| Term 1 | April – September |
| Term 2 | October – December |
| Term 3 | January – March |

The current academic year is auto-created on first startup. Additional years can be created and managed under **Settings**. Switching the active year resets the at-risk view to the new year's term window; all historical data remains accessible.

---

## At-Risk Detection

The `_detect()` function (in `at_risk.py`, reused in the student detail view) evaluates entries within the selected term:

- **Stuck** — last 3 consecutive weeks all "Working Towards"
- **Declining** — strict downward trend over 3 weeks, or current week is "Working Towards" after being higher
- **Improving** — current ranking is a genuine new high (not a return-to-baseline recovery)

Detection is pure Python — no SQL window functions.

---

## License

MIT
