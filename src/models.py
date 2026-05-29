import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

RANKINGS = ["Working Towards", "Meets Expectations", "Exceeds Expectations"]
RANKING_ORDER = {r: i + 1 for i, r in enumerate(RANKINGS)}


class AcademicYear(db.Model):
    __tablename__ = "academic_years"
    id         = db.Column(db.Integer, primary_key=True)
    label      = db.Column(db.String(20), nullable=False, unique=True)  # e.g. "2026–27"
    start_year = db.Column(db.Integer, nullable=False, unique=True)     # April year, e.g. 2026
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
    name             = db.Column(db.String(20), nullable=False)   # "Term 1", "Term 2", "Term 3"
    start_iso_week   = db.Column(db.Integer, nullable=False)
    start_iso_year   = db.Column(db.Integer, nullable=False)
    end_iso_week     = db.Column(db.Integer, nullable=False)
    end_iso_year     = db.Column(db.Integer, nullable=False)

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
        """Return True if the given ISO week falls within this term."""
        # Compare as (year, week) tuples — fully handles year boundaries
        w = (iso_year, iso_week)
        return (self.start_iso_year, self.start_iso_week) <= w <= (self.end_iso_year, self.end_iso_week)

    __table_args__ = (
        db.UniqueConstraint("academic_year_id", "name", name="uq_term_per_year"),
    )


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Student(db.Model):
    __tablename__ = "students"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    roll_number = db.Column(db.String(20), nullable=False)
    grade       = db.Column(db.String(20), nullable=False)

    entries = db.relationship("WeeklyEntry", back_populates="student",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("roll_number", "grade", name="uq_roll_grade"),
    )


class Subject(db.Model):
    __tablename__ = "subjects"
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(80), nullable=False)
    grade = db.Column(db.String(20), nullable=False)

    entries = db.relationship("WeeklyEntry", back_populates="subject",
                              cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("name", "grade", name="uq_subject_grade"),
    )


class WeeklyEntry(db.Model):
    __tablename__ = "weekly_entries"
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    iso_week   = db.Column(db.Integer, nullable=False)
    iso_year   = db.Column(db.Integer, nullable=False)
    ranking    = db.Column(db.String(30), nullable=False)

    student = db.relationship("Student", back_populates="entries")
    subject = db.relationship("Subject", back_populates="entries")

    __table_args__ = (
        db.UniqueConstraint("student_id", "subject_id", "iso_week", "iso_year",
                            name="uq_entry_per_week"),
    )
