import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

RANKINGS = ["Working Towards", "Meets Expectations", "Exceeds Expectations"]
RANKING_ORDER = {r: i + 1 for i, r in enumerate(RANKINGS)}
ROLES = ["teacher", "incharge", "coordinator", "admin"]


# ---------------------------------------------------------------------------
# User & roles
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # Stored as 'active' to avoid collision with Flask-Login's is_active property
    active        = db.Column(db.Boolean, nullable=False, default=True)

    roles      = db.relationship("UserRole", back_populates="user",
                                 cascade="all, delete-orphan", lazy="joined")
    audit_logs = db.relationship("AuditLog", back_populates="user", lazy="dynamic")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # Flask-Login checks .is_active; delegate to our column
    @property
    def is_active(self):
        return self.active

    @is_active.setter
    def is_active(self, value):
        self.active = value

    @property
    def role_names(self):
        return {r.role for r in self.roles}

    def has_role(self, *roles):
        return bool(self.role_names & set(roles))

    def display_roles(self):
        """Human-readable role labels for the UI."""
        labels = []
        rnames = self.role_names
        if "admin" in rnames:
            labels.append("Admin")
        if "coordinator" in rnames:
            labels.append("Coordinator")
        for g in sorted({r.grade for r in self.roles if r.role == "incharge" and r.grade}):
            labels.append(f"In-Charge ({g})")
        tc = sum(1 for r in self.roles if r.role == "teacher")
        if tc:
            labels.append(f"Teacher ({tc} assignment{'s' if tc != 1 else ''})")
        return labels or ["No role assigned"]


class UserRole(db.Model):
    __tablename__ = "user_roles"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role       = db.Column(db.String(20), nullable=False)  # teacher|incharge|coordinator|admin
    grade      = db.Column(db.String(20), nullable=True)   # None for coordinator/admin
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=True)

    user    = db.relationship("User", back_populates="roles")
    subject = db.relationship("Subject")

    __table_args__ = (
        db.UniqueConstraint("user_id", "role", "grade", "subject_id",
                            name="uq_user_role_assignment"),
    )


# ---------------------------------------------------------------------------
# Academic structure
# ---------------------------------------------------------------------------

class AcademicYear(db.Model):
    __tablename__ = "academic_years"
    id         = db.Column(db.Integer, primary_key=True)
    label      = db.Column(db.String(20), nullable=False, unique=True)
    start_year = db.Column(db.Integer, nullable=False, unique=True)
    is_active  = db.Column(db.Boolean, nullable=False, default=False)

    terms = db.relationship("Term", back_populates="academic_year",
                            cascade="all, delete-orphan", order_by="Term.id")

    @property
    def end_year(self):
        return self.start_year + 1


class Term(db.Model):
    __tablename__ = "terms"
    id               = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    name             = db.Column(db.String(20), nullable=False)
    start_iso_week   = db.Column(db.Integer, nullable=False)
    start_iso_year   = db.Column(db.Integer, nullable=False)
    end_iso_week     = db.Column(db.Integer, nullable=False)
    end_iso_year     = db.Column(db.Integer, nullable=False)
    is_locked        = db.Column(db.Boolean, nullable=False, default=False)

    academic_year = db.relationship("AcademicYear", back_populates="terms")

    @property
    def start_date(self):
        try:
            return datetime.date.fromisocalendar(self.start_iso_year, self.start_iso_week, 1)
        except ValueError:
            return None

    @property
    def end_date(self):
        try:
            return datetime.date.fromisocalendar(self.end_iso_year, self.end_iso_week, 7)
        except ValueError:
            return None

    def contains_week(self, iso_week: int, iso_year: int) -> bool:
        w = (iso_year, iso_week)
        return (self.start_iso_year, self.start_iso_week) <= w <= (self.end_iso_year, self.end_iso_week)

    __table_args__ = (
        db.UniqueConstraint("academic_year_id", "name", name="uq_term_per_year"),
    )


# ---------------------------------------------------------------------------
# Grade registry
# ---------------------------------------------------------------------------

class Grade(db.Model):
    __tablename__ = "grades"
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(20), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Students & subjects
# ---------------------------------------------------------------------------

class Student(db.Model):
    __tablename__ = "students"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    roll_number = db.Column(db.String(20), nullable=False)
    grade       = db.Column(db.String(20), nullable=False)
    is_active   = db.Column(db.Boolean, nullable=False, default=True)

    entries = db.relationship("WeeklyEntry", back_populates="student",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("roll_number", "grade", name="uq_roll_grade"),
    )


class Subject(db.Model):
    __tablename__ = "subjects"
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(80), nullable=False)
    grade     = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    entries = db.relationship("WeeklyEntry", back_populates="subject",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("name", "grade", name="uq_subject_grade"),
    )


# ---------------------------------------------------------------------------
# Weekly entries (with audit fields)
# ---------------------------------------------------------------------------

class WeeklyEntry(db.Model):
    __tablename__ = "weekly_entries"
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    iso_week   = db.Column(db.Integer, nullable=False)
    iso_year   = db.Column(db.Integer, nullable=False)
    ranking    = db.Column(db.String(30), nullable=False)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship("Student", back_populates="entries")
    subject = db.relationship("Subject", back_populates="entries")

    __table_args__ = (
        db.UniqueConstraint("student_id", "subject_id", "iso_week", "iso_year",
                            name="uq_entry_per_week"),
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    timestamp  = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    action     = db.Column(db.String(10), nullable=False)   # "create" | "edit"
    model_name = db.Column(db.String(40), nullable=False)
    record_id  = db.Column(db.Integer, nullable=False)
    field_name = db.Column(db.String(40), nullable=True)
    old_value  = db.Column(db.String(200), nullable=True)
    new_value  = db.Column(db.String(200), nullable=True)
    note       = db.Column(db.String(200), nullable=True)

    user = db.relationship("User", back_populates="audit_logs")


# ---------------------------------------------------------------------------
# App config (key-value store for settings like grace_period_hours)
# ---------------------------------------------------------------------------

class AppConfig(db.Model):
    __tablename__ = "app_config"
    key   = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200), nullable=False)

    @classmethod
    def get_value(cls, key, default=None):
        row = db.session.get(cls, key)
        return row.value if row else default

    @classmethod
    def set_value(cls, key, value):
        row = db.session.get(cls, key)
        if row:
            row.value = str(value)
        else:
            db.session.add(cls(key=key, value=str(value)))
