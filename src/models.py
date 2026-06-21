import calendar
import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

RANKINGS = ["Working Towards", "Meets Expectations", "Exceeds Expectations"]
RANKING_ORDER = {r: i + 1 for i, r in enumerate(RANKINGS)}
ROLES = ["teacher", "incharge", "coordinator", "admin"]

# Tiering thresholds (stuck rubric count per subject)
WARNING_THRESHOLD  = 1   # 1–2 stuck rubrics → Warning
AT_RISK_THRESHOLD  = 3   # 3+ stuck rubrics  → At-Risk


# ---------------------------------------------------------------------------
# Fortnight helpers
# ---------------------------------------------------------------------------

def date_to_fortnight(d: datetime.date) -> tuple[int, int, int]:
    """Return (year, month, period) where period is 1 (1–15) or 2 (16–end)."""
    return d.year, d.month, 1 if d.day <= 15 else 2


def fortnight_start(year: int, month: int, period: int) -> datetime.date:
    return datetime.date(year, month, 1 if period == 1 else 16)


def fortnight_end(year: int, month: int, period: int) -> datetime.date:
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 15 if period == 1 else last)


def fortnight_label(year: int, month: int, period: int) -> str:
    end = fortnight_end(year, month, period)
    month_abbr = datetime.date(year, month, 1).strftime("%b")
    if period == 1:
        return f"1–15 {month_abbr} {year}"
    return f"16–{end.day} {month_abbr} {year}"


def prev_fortnight(year: int, month: int, period: int) -> tuple[int, int, int]:
    if period == 2:
        return year, month, 1
    if month == 1:
        return year - 1, 12, 2
    return year, month - 1, 2


def next_fortnight(year: int, month: int, period: int) -> tuple[int, int, int]:
    if period == 1:
        return year, month, 2
    if month == 12:
        return year + 1, 1, 1
    return year, month + 1, 1


# ---------------------------------------------------------------------------
# User & roles
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    active        = db.Column(db.Boolean, nullable=False, default=True)

    roles      = db.relationship("UserRole", back_populates="user",
                                 cascade="all, delete-orphan", lazy="joined")
    audit_logs = db.relationship("AuditLog", back_populates="user", lazy="dynamic")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

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
    role       = db.Column(db.String(20), nullable=False)
    grade      = db.Column(db.String(20), nullable=True)
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
    start_date       = db.Column(db.Date, nullable=False)
    end_date         = db.Column(db.Date, nullable=False)
    is_locked        = db.Column(db.Boolean, nullable=False, default=False)

    academic_year = db.relationship("AcademicYear", back_populates="terms")

    def contains_fortnight(self, year: int, month: int, period: int) -> bool:
        """True if this fortnight overlaps with the term's date range."""
        fs = fortnight_start(year, month, period)
        fe = fortnight_end(year, month, period)
        return self.start_date <= fe and self.end_date >= fs

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

    entries = db.relationship("FortnightEntry", back_populates="student",
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

    rubrics = db.relationship("Rubric", back_populates="subject",
                              cascade="all, delete-orphan",
                              order_by="Rubric.display_order, Rubric.id")
    entries = db.relationship("FortnightEntry", back_populates="subject",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("name", "grade", name="uq_subject_grade"),
    )


# ---------------------------------------------------------------------------
# Rubrics
# ---------------------------------------------------------------------------

class Rubric(db.Model):
    __tablename__ = "rubrics"
    id            = db.Column(db.Integer, primary_key=True)
    subject_id    = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    name          = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.String(200), nullable=True)
    is_required   = db.Column(db.Boolean, nullable=False, default=True)
    is_active     = db.Column(db.Boolean, nullable=False, default=True)
    display_order = db.Column(db.Integer, nullable=False, default=0)

    subject = db.relationship("Subject", back_populates="rubrics")
    entries = db.relationship("FortnightEntry", back_populates="rubric",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("subject_id", "name", name="uq_rubric_per_subject"),
    )


# ---------------------------------------------------------------------------
# Fortnight entries
# ---------------------------------------------------------------------------

class FortnightEntry(db.Model):
    __tablename__ = "fortnight_entries"
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    rubric_id  = db.Column(db.Integer, db.ForeignKey("rubrics.id"), nullable=False)
    ft_year    = db.Column(db.Integer, nullable=False)
    ft_month   = db.Column(db.Integer, nullable=False)
    ft_period  = db.Column(db.Integer, nullable=False)   # 1 = 1st–15th, 2 = 16th–end
    ranking    = db.Column(db.String(30), nullable=False)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    student = db.relationship("Student", back_populates="entries")
    subject = db.relationship("Subject", back_populates="entries")
    rubric  = db.relationship("Rubric",  back_populates="entries")

    __table_args__ = (
        db.UniqueConstraint("student_id", "rubric_id", "ft_year", "ft_month", "ft_period",
                            name="uq_entry_per_fortnight"),
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    timestamp  = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    action     = db.Column(db.String(10), nullable=False)
    model_name = db.Column(db.String(40), nullable=False)
    record_id  = db.Column(db.Integer, nullable=False)
    field_name = db.Column(db.String(40), nullable=True)
    old_value  = db.Column(db.String(200), nullable=True)
    new_value  = db.Column(db.String(200), nullable=True)
    note       = db.Column(db.String(200), nullable=True)

    user = db.relationship("User", back_populates="audit_logs")


# ---------------------------------------------------------------------------
# App config
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
