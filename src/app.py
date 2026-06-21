import os
import datetime
import warnings
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, current_user
from dotenv import load_dotenv
from models import (
    db, User, UserRole, Student, Subject, Grade, Rubric, FortnightEntry,
    AcademicYear, Term, AppConfig,
    date_to_fortnight, fortnight_label,
)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

login_manager = LoginManager()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///database.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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

    from permissions import Permissions

    @app.context_processor
    def inject_perms():
        return {"perms": Permissions(current_user)}

    @app.context_processor
    def inject_topbar():
        if not current_user.is_authenticated:
            return {}
        today = datetime.date.today()
        ft_year, ft_month, ft_period = date_to_fortnight(today)
        label = fortnight_label(ft_year, ft_month, ft_period)

        ay = AcademicYear.query.filter_by(is_active=True).first()
        active_term = None
        ay_label    = None
        if ay:
            ay_label = ay.label
            for t in ay.terms:
                if t.contains_fortnight(ft_year, ft_month, ft_period):
                    active_term = t
                    break

        p = Permissions(current_user)
        pending = None
        if not p.is_admin and not p.is_coordinator:
            pairs = p.enterable_pairs()
            if pairs:
                total_slots, entered = 0, 0
                for grade, sid in pairs:
                    # Count required rubrics × students for this subject
                    req_rubrics = Rubric.query.filter_by(
                        subject_id=sid, is_required=True, is_active=True
                    ).count()
                    students = Student.query.filter_by(
                        grade=grade, is_active=True
                    ).count()
                    total_slots += req_rubrics * students
                    entered += FortnightEntry.query.filter_by(
                        subject_id=sid,
                        ft_year=ft_year, ft_month=ft_month, ft_period=ft_period,
                    ).count()
                pending = max(0, total_slots - entered)

        return {
            "topbar_period_label": label,
            "topbar_term":         active_term,
            "topbar_ay_label":     ay_label,
            "topbar_pending":      pending,
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
    """Safely add new columns to existing tables (SQLite-safe try/except)."""
    migrations = [
        "ALTER TABLE rubrics ADD COLUMN description VARCHAR(200)",
        "ALTER TABLE rubrics ADD COLUMN display_order INTEGER NOT NULL DEFAULT 0",
    ]
    with db.engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(db.text(sql))
                conn.commit()
            except Exception:
                pass  # column already exists


def _seed_grades() -> None:
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
        admin = User.query.filter_by(username="admin").first()
        if admin and not any(r.role == "admin" for r in admin.roles):
            db.session.add(UserRole(user_id=admin.id, role="admin"))
            db.session.commit()


def _seed_default_config() -> None:
    if not db.session.get(AppConfig, "grace_period_hours"):
        db.session.add(AppConfig(key="grace_period_hours", value="48"))
        db.session.commit()


def _seed_default_academic_year() -> None:
    if AcademicYear.query.count() > 0:
        return

    today = datetime.date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    label = f"{start_year}–{str(start_year + 1)[-2:]}"

    ay = AcademicYear(label=label, start_year=start_year, is_active=True)
    db.session.add(ay)
    db.session.flush()

    end_year = start_year + 1
    for td in [
        dict(name="Term 1",
             start_date=datetime.date(start_year, 4, 1),
             end_date=datetime.date(start_year, 9, 30)),
        dict(name="Term 2",
             start_date=datetime.date(start_year, 10, 1),
             end_date=datetime.date(start_year, 12, 31)),
        dict(name="Term 3",
             start_date=datetime.date(end_year, 1, 1),
             end_date=datetime.date(end_year, 3, 31)),
    ]:
        db.session.add(Term(academic_year_id=ay.id, **td))

    db.session.commit()
    print(f"[init] Academic year {label} created and set as active.")


def _seed_demo() -> None:  # noqa: C901
    """
    Seed Grade 6 demo data:
      30 students (15 normal · 5 warning · 5 at-risk · 5 improving)
      6 subjects with rubrics (some optional)
      6 calendar fortnights of Term 1 (Apr–Jun 2026)
      6 teacher accounts mapped to Grade 6 subjects
    """
    if Subject.query.filter_by(grade="Grade 6").first():
        return   # already seeded

    import random
    import datetime as dt

    WT = "Working Towards"
    ME = "Meets Expectations"
    EE = "Exceeds Expectations"

    # ── Fortnights: (year, month, period, entry_datetime) ─────────────────
    FTS = [
        (2026, 4, 1, dt.datetime(2026,  4, 10,  9,  0)),
        (2026, 4, 2, dt.datetime(2026,  4, 24, 10, 30)),
        (2026, 5, 1, dt.datetime(2026,  5,  8,  9, 15)),
        (2026, 5, 2, dt.datetime(2026,  5, 22, 11,  0)),
        (2026, 6, 1, dt.datetime(2026,  6,  6,  9, 45)),
        (2026, 6, 2, dt.datetime(2026,  6, 19, 10,  0)),
    ]
    N = len(FTS)

    # ── Grade ─────────────────────────────────────────────────────────────
    if not Grade.query.filter_by(name="Grade 6").first():
        db.session.add(Grade(name="Grade 6", is_active=True))
        db.session.flush()

    # ── Academic year: use active one (already seeded by _seed_default_academic_year) ──
    ay = AcademicYear.query.filter_by(is_active=True).first()
    if not ay:
        ay = AcademicYear(label="2026–27", start_year=2026, is_active=True)
        db.session.add(ay)
        db.session.flush()
        for td in [
            dict(name="Term 1", start_date=dt.date(2026, 4, 1),  end_date=dt.date(2026, 9, 30)),
            dict(name="Term 2", start_date=dt.date(2026, 10, 1), end_date=dt.date(2026, 12, 31)),
            dict(name="Term 3", start_date=dt.date(2027, 1, 1),  end_date=dt.date(2027, 3, 31)),
        ]:
            db.session.add(Term(academic_year_id=ay.id, **td))
        db.session.flush()

    # ── Subjects + Rubrics ────────────────────────────────────────────────
    # (subject_name, [(rubric_name, is_required), ...])
    SUBJECTS_DEF = [
        ("Mathematical Reasoning & Logic", [
            ("Logical Reasoning",                             True),
            ("Numerical Problem Solving",                    True),
            ("Arithmetic: Decimals, Fractions & %",          True),
            ("Algebraic Expressions & Inequalities",         True),
            ("Geometry: Area, Volume & Perimeter",           True),
            ("Data Handling & Interpretation",               True),
            ("Ratio & Proportion",                           True),
            ("Sequences & Mathematical Justification",       False),  # optional
        ]),
        ("Science", [
            ("Models & Diagrams",       True),
            ("Experiments & Analysis",  True),
            ("Scientific Writing",      True),
            ("Scientific Terminology",  True),
            ("Real-Life Connections",   False),  # optional
        ]),
        ("English, Language & Literacy", [
            ("Vocabulary & Word Structure",       True),
            ("Grammar, Punctuation & Structure",  True),
            ("Planning & Writing",                True),
            ("Interpreting Texts",                True),
            ("Standard English",                  True),
        ]),
        ("Computing", [
            ("Algorithms & Flowcharts",                    True),
            ("Block Programming: Selection & Comparison",  True),
            ("Variables & Data",                           True),
            ("Scratch Game Creation",                      True),
            ("Software Literacy",                          True),
            ("IP, DNS & Networking",                       True),
            ("Online Safety & Privacy",                    True),
        ]),
        ("Geography & History", [
            ("Civics & History Interest",       True),
            ("Earth's Features",                True),
            ("Constitution & Human Rights",     True),
            ("Elections & Election Commission", True),
            ("Sustainability Initiatives",      True),
            ("Peer-Teaching Contribution",      False),  # optional
            ("CSR Understanding",               False),  # optional
        ]),
        ("Art & Design", [
            ("Creative Expression",           True),
            ("Design Awareness",              True),
            ("Application of Skills",         True),
            ("Collaboration & Empathy",       True),
            ("Responsibility & Organisation", True),
            ("Cultural Sensitivity",          False),  # optional
        ]),
    ]

    subjects: dict = {}        # name → Subject
    rubrics_map: dict = {}     # name → [Rubric]  in display_order

    for s_name, r_defs in SUBJECTS_DEF:
        subj = Subject(name=s_name, grade="Grade 6", is_active=True)
        db.session.add(subj)
        db.session.flush()
        subjects[s_name] = subj
        rubrics_map[s_name] = []
        for r_idx, (r_name, r_req) in enumerate(r_defs):
            rub = Rubric(
                subject_id=subj.id, name=r_name,
                is_required=r_req, is_active=True, display_order=r_idx,
            )
            db.session.add(rub)
            rubrics_map[s_name].append(rub)
        db.session.flush()

    # ── Students ──────────────────────────────────────────────────────────
    NAMES = [
        # 0-14  Normal
        "Aarav Sharma",    "Priya Nair",      "Rohan Menon",
        "Divya Pillai",    "Arjun Reddy",     "Meera Krishnan",
        "Karthik Iyer",    "Ananya Gupta",    "Vikram Patel",
        "Sanya Joshi",     "Aditya Singh",    "Pooja Varma",
        "Nikhil Bhat",     "Lavanya Rao",     "Rahul Chandra",
        # 15-19 Warning  (1-2 rubrics stuck in one subject)
        "Ishaan Mathur",   "Tanya Desai",     "Vihaan Kulkarni",
        "Ridhi Mehta",     "Aryaman Shetty",
        # 20-24 At-Risk  (3+ rubrics stuck in one subject)
        "Devika Pillai",   "Siddharth Nair",  "Kriti Verma",
        "Yash Tripathi",   "Amara Joshi",
        # 25-29 Improving (clear upward trend)
        "Neel Kapoor",     "Shreya Mishra",   "Kabir Rao",
        "Zara Ahmed",      "Tanvi Bose",
    ]
    students = []
    for i, name in enumerate(NAMES):
        s = Student(
            name=name, grade="Grade 6",
            roll_number=f"6{i+1:03d}", is_active=True,
        )
        db.session.add(s)
        students.append(s)
    db.session.flush()

    # ── Teachers (one per subject) ────────────────────────────────────────
    TEACHER_DEFS = [
        ("teacher_math_g6",  "MathG6#1",  "Mathematical Reasoning & Logic"),
        ("teacher_sci_g6",   "SciG6#1",   "Science"),
        ("teacher_eng_g6",   "EngG6#1",   "English, Language & Literacy"),
        ("teacher_comp_g6",  "CompG6#1",  "Computing"),
        ("teacher_gh_g6",    "GHG6#1",    "Geography & History"),
        ("teacher_art_g6",   "ArtG6#1",   "Art & Design"),
    ]
    teacher_by: dict = {}  # subject_name → User
    for uname, pw, s_name in TEACHER_DEFS:
        u = User.query.filter_by(username=uname).first()
        if not u:
            u = User(username=uname)
            u.set_password(pw)
            db.session.add(u)
            db.session.flush()
            db.session.add(UserRole(
                user_id=u.id, role="teacher",
                grade="Grade 6", subject_id=subjects[s_name].id,
            ))
            db.session.flush()
        teacher_by[s_name] = u

    # ── Ranking patterns (6 fortnights each) ─────────────────────────────
    # Notation: list of N ranking strings or None (no entry = blank)
    STUCK    = [WT, WT, WT, WT, WT, WT]       # last3=[WT,WT,WT]   → 'stuck'
    DECLINE  = [EE, EE, ME, ME, ME, WT]        # last3=[ME,ME,WT]   → 'declining'
    IMPROV1  = [WT, WT, ME, ME, EE, EE]        # last3=[ME,EE,EE]   → clean (not flagged)
    IMPROV2  = [WT, ME, ME, EE, EE, EE]        # last3=[EE,EE,EE]   → clean
    IMPROV3  = [WT, WT, WT, ME, ME, EE]        # last3=[ME,ME,EE]   → clean

    def rand_good(seed: int) -> list:
        """Healthy ME/EE mix, never stuck/declining in the last 3 entries."""
        rng = random.Random(seed)
        pool = [ME, ME, ME, EE, EE]             # no WT in pool → last3 always ≥ ME
        pat  = [rng.choice(pool) for _ in range(N)]
        # Occasionally a single WT early (positions 0..N-4) for realism
        if rng.random() < 0.30:
            pat[rng.randint(0, N - 4)] = WT
        return pat

    def rand_mid(seed: int) -> list:
        """Below-average mix with WT, but last entry always ME/EE to avoid false flags."""
        rng = random.Random(seed)
        pool = [WT, ME, ME, EE]
        pat  = [rng.choice(pool) for _ in range(N)]
        if pat[-1] == WT:                        # prevent 'declining' from tail WT
            pat[-1] = ME
        return pat

    def optional_blanks(pat: list, seed: int, prob: float = 0.40) -> list:
        """Randomly blank some entries for optional rubrics."""
        rng = random.Random(seed + 9_000)
        return [None if rng.random() < prob else v for v in pat]

    # ── Warning student config: subject → {rubric_idx: pattern} ──────────
    # Each student has at most 2 flagged rubrics in one subject → stuck_count ≤ 2 → 'warning'
    WARNING_FLAGS: dict[int, tuple] = {
        15: ("Mathematical Reasoning & Logic",  {0: STUCK,   1: STUCK}),    # 2 stuck
        16: ("Science",                          {0: STUCK}),                # 1 stuck
        17: ("English, Language & Literacy",    {2: STUCK,   3: DECLINE}),  # 2 flagged
        18: ("Computing",                        {3: STUCK}),                # 1 stuck
        19: ("Geography & History",             {1: STUCK,   2: DECLINE}),  # 2 flagged
    }

    # ── At-Risk student config: subject → [rubric indices that are flagged] ──
    # rubrics 0,1,2 → STUCK; rubric 3 → DECLINE  → stuck_count ≥ 3 → 'at_risk'
    AT_RISK_TARGETS: dict[int, str] = {
        20: "Mathematical Reasoning & Logic",
        21: "Science",
        22: "Computing",
        23: "English, Language & Literacy",
        24: "Geography & History",
    }

    # ── Improving student base patterns (one per student) ─────────────────
    IMPROV_BASE = [IMPROV1, IMPROV2, IMPROV3, IMPROV1, IMPROV2]

    # ── Pattern resolver ──────────────────────────────────────────────────
    def get_pattern(s_idx: int, s_name: str, r_idx: int, rubric: Rubric) -> list:
        is_opt = not rubric.is_required
        s_hash = sum(ord(c) for c in s_name) % 10007
        seed   = s_idx * 7919 + r_idx * 997 + s_hash

        # --- Improving (25-29) -------------------------------------------
        if s_idx >= 25:
            base = list(IMPROV_BASE[s_idx - 25])
            # Vary later rubrics: mostly ME heading to EE
            if r_idx >= 3:
                base = [ME if v == WT else v for v in base]
                base[-1] = EE
            if is_opt:
                return optional_blanks(base, seed, prob=0.35)
            # Simulate FT6 not yet entered for ~25% of rubrics
            result = base[:]
            if r_idx % 4 == 0:
                result[-1] = None
            return result

        # --- At-Risk (20-24) ---------------------------------------------
        if s_idx in AT_RISK_TARGETS:
            target = AT_RISK_TARGETS[s_idx]
            if s_name == target:
                if r_idx < 3:
                    pat = list(STUCK)             # 'stuck'
                elif r_idx == 3:
                    pat = list(DECLINE)           # 'declining'
                else:
                    pat = rand_mid(seed)
                if is_opt:
                    return optional_blanks(pat, seed)
                return pat
            # Non-target subjects: below-average but not flagged
            pat = rand_mid(seed + 500)
            if is_opt:
                return optional_blanks(pat, seed)
            return pat

        # --- Warning (15-19) ---------------------------------------------
        if s_idx in WARNING_FLAGS:
            target, flagged = WARNING_FLAGS[s_idx]
            if s_name == target and r_idx in flagged:
                pat = list(flagged[r_idx])
                if is_opt:
                    return optional_blanks(pat, seed)
                return pat
            pat = rand_good(seed)
            if is_opt:
                return optional_blanks(pat, seed)
            return pat

        # --- Normal (0-14) -----------------------------------------------
        pat = rand_good(seed) if s_idx < 10 else rand_mid(seed)
        if is_opt:
            return optional_blanks(pat, seed)
        # ~8% chance of one missed required entry mid-term (not FT6)
        if random.Random(seed + 1).random() < 0.08:
            gap = random.Random(seed + 2).randint(2, N - 2)
            pat[gap] = None
        return pat

    # ── Generate entries ──────────────────────────────────────────────────
    total = 0
    for s_idx, student in enumerate(students):
        for s_name, _ in SUBJECTS_DEF:
            subj    = subjects[s_name]
            creator = teacher_by[s_name]
            for r_idx, rubric in enumerate(rubrics_map[s_name]):
                pattern = get_pattern(s_idx, s_name, r_idx, rubric)
                for ft_idx, (fy, fm, fp, entry_dt) in enumerate(FTS):
                    if ft_idx >= len(pattern):
                        continue
                    ranking = pattern[ft_idx]
                    if ranking is None:
                        continue
                    db.session.add(FortnightEntry(
                        student_id=student.id,
                        subject_id=subj.id,
                        rubric_id=rubric.id,
                        ft_year=fy, ft_month=fm, ft_period=fp,
                        ranking=ranking,
                        created_by=creator.id,  created_at=entry_dt,
                        updated_by=creator.id,  updated_at=entry_dt,
                    ))
                    total += 1

    db.session.commit()
    print(
        f"[seed] Grade 6 demo: 30 students · 6 subjects · "
        f"{sum(len(v) for v in rubrics_map.values())} rubrics · "
        f"{total} fortnight entries."
    )


# Module-level app instance — required by Gunicorn (src.app:app).
# When run directly (python app.py) __name__ == "__main__" is also true,
# so app.run() below picks up the same object.
app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
