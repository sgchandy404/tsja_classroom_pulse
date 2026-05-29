import os
import random
from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager
from models import db, User, Student, Subject, WeeklyEntry, RANKINGS

login_manager = LoginManager()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

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
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entry_bp)
    app.register_blueprint(at_risk_bp)
    app.register_blueprint(students_bp)

    with app.app_context():
        db.create_all()
        _seed_admin()
        if os.getenv("SEED_DEMO_DATA") == "1" and Subject.query.count() == 0:
            _seed_demo()

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    return app


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def _seed_admin() -> None:
    if User.query.count() == 0:
        admin = User(username="admin")
        admin.set_password("changeme123")
        db.session.add(admin)
        db.session.commit()
        print("[init] Default admin created — username: admin  password: changeme123")


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
    print("[seed] Demo data loaded — 3 grades, 20 students, 6 weeks of entries.")
    print("[seed] At-risk scenarios: Aarav (stuck), Priya (declining), Sanjay (stuck), Dev (declining).")


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
