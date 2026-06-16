import os
import random
import datetime
import warnings
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, current_user
from dotenv import load_dotenv
from models import db, User, UserRole, Student, Subject, Grade, WeeklyEntry, AcademicYear, Term, AppConfig, RANKINGS

# Load .env from the project root (one level up from src/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

login_manager = LoginManager()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    # Database — default to SQLite in src/instance/; override with DATABASE_URL
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///database.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Secret key — required in production
    secret = os.getenv("SECRET_KEY")
    if not secret:
        warnings.warn(
            "SECRET_KEY env var not set — using an insecure default. "
            "Set SECRET_KEY in production.",
            stacklevel=2,
        )
        secret = "dev-secret-change-me"
    app.config["SECRET_KEY"] = secret

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "info"

    from auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.entry import entry_bp
    from routes.at_risk import at_risk_bp
    from routes.students import students_bp
    from routes.settings import settings_bp
    from routes.admin import admin_bp
    from routes.audit import audit_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entry_bp)
    app.register_blueprint(at_risk_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(audit_bp)

    # Inject Permissions object into every template
    from permissions import Permissions
    @app.context_processor
    def inject_perms():
        return {"perms": Permissions(current_user)}

    # Inject topbar context: current week, active term, entries pending
    @app.context_processor
    def inject_topbar():
        if not current_user.is_authenticated:
            return {}
        today = datetime.date.today()
        iso   = today.isocalendar()
        week, year = iso.week, iso.year
        try:
            monday = datetime.date.fromisocalendar(year, week, 1)
            sunday = monday + datetime.timedelta(days=6)
            week_label = f"Wk {week} · {monday.strftime('%d %b')} – {sunday.strftime('%d %b')}"
        except ValueError:
            week_label = f"Week {week}"
        ay = AcademicYear.query.filter_by(is_active=True).first()
        active_term = None
        ay_label    = None
        if ay:
            ay_label = ay.label
            for t in ay.terms:
                if t.contains_week(week, year):
                    active_term = t
                    break
        p = Permissions(current_user)
        pending = None
        if not p.is_admin and not p.is_coordinator:
            pairs = p.enterable_pairs()
            if pairs:
                total_slots, entered = 0, 0
                for grade, sid in pairs:
                    total_slots += Student.query.filter_by(grade=grade, is_active=True).count()
                    entered     += WeeklyEntry.query.filter_by(
                        subject_id=sid, iso_week=week, iso_year=year
                    ).count()
                pending = max(0, total_slots - entered)
        return {
            "topbar_week_label": week_label,
            "topbar_term":       active_term,
            "topbar_ay_label":   ay_label,
            "topbar_pending":    pending,
        }

    with app.app_context():
        db.create_all()
        _migrate_schema()
        _seed_admin()
        _seed_default_academic_year()
        _seed_default_config()
        _seed_grades()
        if os.getenv("SEED_DEMO_DATA") == "1" and Subject.query.count() == 0:
            _seed_demo()

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    return app


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def _migrate_schema() -> None:
    """Add new columns/tables to existing DBs without losing data (SQLite-safe)."""
    with db.engine.connect() as conn:
        for stmt in [
            "ALTER TABLE students ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE subjects ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
        ]:
            try:
                conn.execute(db.text(stmt))
                conn.commit()
            except Exception:
                pass  # column already exists


def _seed_grades() -> None:
    """Auto-migrate existing grade strings into the Grade registry if it is empty."""
    if Grade.query.count() > 0:
        return
    existing = set(
        [r[0] for r in db.session.query(Student.grade).distinct().all()] +
        [r[0] for r in db.session.query(Subject.grade).distinct().all()]
    )
    for name in sorted(existing):
        if name:
            db.session.add(Grade(name=name, is_active=True))
    if existing:
        db.session.commit()
        print(f"[init] Seeded {len(existing)} grade(s) into Grade registry.")


def _seed_admin() -> None:
    if User.query.count() == 0:
        default_pw = os.getenv("ADMIN_PASSWORD", "changeme123")
        admin = User(username="admin")
        admin.set_password(default_pw)
        db.session.add(admin)
        db.session.flush()
        db.session.add(UserRole(user_id=admin.id, role="admin"))
        db.session.commit()
        print(f"[init] Default admin created — username: admin  password: {default_pw}")
    else:
        # Ensure the admin user has an admin role (migration for existing DBs)
        admin = User.query.filter_by(username="admin").first()
        if admin and not any(r.role == "admin" for r in admin.roles):
            db.session.add(UserRole(user_id=admin.id, role="admin"))
            db.session.commit()


def _seed_default_config() -> None:
    """Seed default AppConfig values if not already present."""
    if not db.session.get(AppConfig, "grace_period_hours"):
        db.session.add(AppConfig(key="grace_period_hours", value="48"))
        db.session.commit()


def _build_default_terms(start_year: int) -> list[dict]:
    """Return Term kwargs for an Indian Cambridge-affiliated school calendar."""
    end_year = start_year + 1

    def _w(year, month, day):
        iso = datetime.date(year, month, day).isocalendar()
        return iso.week, iso.year

    t1s, t1sy = _w(start_year, 4,  1)
    t1e, t1ey = _w(start_year, 9, 30)
    t2s, t2sy = _w(start_year, 10, 1)
    t2e, t2ey = _w(start_year, 12, 31)
    t3s, t3sy = _w(end_year,   1,  1)
    t3e, t3ey = _w(end_year,   3, 31)

    return [
        dict(name="Term 1", start_iso_week=t1s, start_iso_year=t1sy, end_iso_week=t1e, end_iso_year=t1ey),
        dict(name="Term 2", start_iso_week=t2s, start_iso_year=t2sy, end_iso_week=t2e, end_iso_year=t2ey),
        dict(name="Term 3", start_iso_week=t3s, start_iso_year=t3sy, end_iso_week=t3e, end_iso_year=t3ey),
    ]


def _seed_default_academic_year() -> None:
    """Create and activate the current academic year if none exist."""
    if AcademicYear.query.count() > 0:
        return

    today = datetime.date.today()
    # Academic year starts in April; if we're before April it's the previous year's start
    start_year = today.year if today.month >= 4 else today.year - 1
    label = f"{start_year}–{str(start_year + 1)[-2:]}"

    ay = AcademicYear(label=label, start_year=start_year, is_active=True)
    db.session.add(ay)
    db.session.flush()

    for td in _build_default_terms(start_year):
        db.session.add(Term(academic_year_id=ay.id, **td))

    db.session.commit()
    print(f"[init] Academic year {label} created and set as active.")


def _seed_demo() -> None:
    random.seed(42)

    GRADES = {
        "Grade 5A": {
            "subjects": ["Mathematics", "English", "Science", "Social Studies", "Art"],
            "students": [
                ("Aarav Sharma",    "5A-001"),
                ("Priya Nair",      "5A-002"),
                ("Rohan Menon",     "5A-003"),
                ("Ananya Pillai",   "5A-004"),
                ("Kiran Thomas",    "5A-005"),
                ("Divya Krishnan",  "5A-006"),
                ("Arjun Iyer",      "5A-007"),
            ],
        },
        "Grade 6B": {
            "subjects": ["Mathematics", "English", "Science", "History", "Physical Education"],
            "students": [
                ("Meera Reddy",     "6B-001"),
                ("Sanjay Kumar",    "6B-002"),
                ("Lakshmi Rao",     "6B-003"),
                ("Vikram Singh",    "6B-004"),
                ("Neha Patel",      "6B-005"),
                ("Rahul Verma",     "6B-006"),
                ("Aisha Siddiqui",  "6B-007"),
            ],
        },
        "Grade 7C": {
            "subjects": ["Mathematics", "English", "Science", "Geography", "Computer Science"],
            "students": [
                ("Dev Kapoor",      "7C-001"),
                ("Sneha Joshi",     "7C-002"),
                ("Aryan Mehta",     "7C-003"),
                ("Pooja Desai",     "7C-004"),
                ("Kabir Malhotra",  "7C-005"),
                ("Riya Chopra",     "7C-006"),
            ],
        },
    }

    # Weeks: last 6 weeks ending at week 22, 2026
    BASE_YEAR = 2026
    WEEKS = list(range(17, 23))  # weeks 17-22

    # Seed Grade registry
    for grade_name in GRADES:
        if not Grade.query.filter_by(name=grade_name).first():
            db.session.add(Grade(name=grade_name, is_active=True))
    db.session.flush()

    # Insert subjects and students
    subject_objs = {}
    student_objs = {}
    for grade, data in GRADES.items():
        for subj_name in data["subjects"]:
            s = Subject(name=subj_name, grade=grade)
            db.session.add(s)
            subject_objs[(grade, subj_name)] = s

        for name, roll in data["students"]:
            st = Student(name=name, roll_number=roll, grade=grade)
            db.session.add(st)
            student_objs[(grade, roll)] = st

    db.session.flush()  # get IDs

    # Controlled at-risk scenarios
    # Aarav Sharma (5A-001) — stuck in Mathematics for last 3 weeks
    # Priya Nair (5A-002)   — declining in Science: EE→ME→WT over last 3 weeks
    # Sanjay Kumar (6B-002) — stuck in Mathematics for last 3 weeks
    # Dev Kapoor (7C-001)   — declining in Computer Science: EE→ME→WT

    at_risk_scenarios = {
        ("Grade 5A", "5A-001", "Mathematics"):        ["ME", "WT", "WT", "WT", "WT", "WT"],
        ("Grade 5A", "5A-002", "Science"):            ["EE", "EE", "EE", "EE", "ME", "WT"],
        ("Grade 6B", "6B-002", "Mathematics"):        ["ME", "WT", "WT", "WT", "WT", "WT"],
        ("Grade 7C", "7C-001", "Computer Science"):   ["EE", "ME", "ME", "EE", "ME", "WT"],
    }

    RANK_MAP = {"WT": "Working Towards", "ME": "Meets Expectations", "EE": "Exceeds Expectations"}
    WEIGHTS = [0.15, 0.45, 0.40]  # realistic distribution skewing toward ME/EE

    for grade, data in GRADES.items():
        for subj_name in data["subjects"]:
            subj = subject_objs[(grade, subj_name)]
            for name, roll in data["students"]:
                student = student_objs[(grade, roll)]
                for i, week in enumerate(WEEKS):
                    scenario_key = (grade, roll, subj_name)
                    if scenario_key in at_risk_scenarios:
                        ranking = RANK_MAP[at_risk_scenarios[scenario_key][i]]
                    else:
                        ranking = random.choices(RANKINGS, weights=WEIGHTS, k=1)[0]
                    db.session.add(WeeklyEntry(
                        student_id=student.id,
                        subject_id=subj.id,
                        iso_week=week,
                        iso_year=BASE_YEAR,
                        ranking=ranking,
                    ))

    db.session.commit()

    # Demo users for RBAC testing (skip if already exist)
    if not User.query.filter_by(username="teacher_5a").first():
        maths_5a = subject_objs[("Grade 5A", "Mathematics")]
        science_5a = subject_objs[("Grade 5A", "Science")]

        t = User(username="teacher_5a"); t.set_password("test123")
        db.session.add(t); db.session.flush()
        db.session.add(UserRole(user_id=t.id, role="teacher", grade="Grade 5A", subject_id=maths_5a.id))
        db.session.add(UserRole(user_id=t.id, role="teacher", grade="Grade 5A", subject_id=science_5a.id))

        ic = User(username="incharge_5a"); ic.set_password("test123")
        db.session.add(ic); db.session.flush()
        db.session.add(UserRole(user_id=ic.id, role="incharge", grade="Grade 5A"))
        db.session.add(UserRole(user_id=ic.id, role="teacher", grade="Grade 5A", subject_id=maths_5a.id))

        co = User(username="coordinator"); co.set_password("test123")
        db.session.add(co); db.session.flush()
        db.session.add(UserRole(user_id=co.id, role="coordinator"))

        db.session.commit()
        print("[seed] Demo users: teacher_5a, incharge_5a, coordinator (password: test123)")

    print("[seed] Demo data loaded — 3 grades, 20 students, 6 weeks of entries.")
    print("[seed] At-risk scenarios: Aarav (stuck), Priya (declining), Sanjay (stuck), Dev (declining).")


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
