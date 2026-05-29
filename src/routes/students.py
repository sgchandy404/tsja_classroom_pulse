from collections import defaultdict
from flask import Blueprint, render_template
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKING_ORDER
from routes.at_risk import _detect

students_bp = Blueprint("students", __name__, url_prefix="/students")


@students_bp.route("/<int:student_id>")
@login_required
def detail(student_id):
    student = Student.query.get_or_404(student_id)

    # All entries for this student, oldest first
    entries = (
        WeeklyEntry.query
        .filter_by(student_id=student_id)
        .order_by(WeeklyEntry.iso_year, WeeklyEntry.iso_week)
        .all()
    )

    # Collect ordered unique weeks and subjects
    weeks_seen = []
    weeks_set = set()
    for e in entries:
        key = (e.iso_year, e.iso_week)
        if key not in weeks_set:
            weeks_seen.append(key)
            weeks_set.add(key)

    subjects = (
        Subject.query
        .filter_by(grade=student.grade)
        .order_by(Subject.name)
        .all()
    )

    # Build lookup: (subject_id, year, week) -> ranking
    lookup = {(e.subject_id, e.iso_year, e.iso_week): e.ranking for e in entries}

    # Per-subject history for at-risk check and sparkline
    subject_rows = []
    entries_by_subject = defaultdict(list)
    for e in entries:
        entries_by_subject[e.subject_id].append(e)

    for subject in subjects:
        subj_entries = entries_by_subject[subject.id]
        is_risk, reason = _detect(subj_entries)
        history = [
            {
                "year": year,
                "week": week,
                "ranking": lookup.get((subject.id, year, week)),
            }
            for year, week in weeks_seen
        ]
        subject_rows.append({
            "subject":   subject,
            "history":   history,
            "is_risk":   is_risk,
            "reason":    reason,
            "latest":    subj_entries[-1].ranking if subj_entries else None,
        })

    # Grade-mates for quick nav (prev/next alphabetically)
    grade_mates = (
        Student.query
        .filter_by(grade=student.grade)
        .order_by(Student.name)
        .all()
    )
    ids = [s.id for s in grade_mates]
    idx = ids.index(student_id)
    prev_student = grade_mates[idx - 1] if idx > 0 else None
    next_student = grade_mates[idx + 1] if idx < len(grade_mates) - 1 else None

    return render_template(
        "students/detail.html",
        student=student,
        weeks=weeks_seen,
        subject_rows=subject_rows,
        prev_student=prev_student,
        next_student=next_student,
        ranking_order=RANKING_ORDER,
    )
