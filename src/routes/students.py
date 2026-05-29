import json
from collections import defaultdict
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, AcademicYear, Term, RANKING_ORDER
from routes.at_risk import _detect, _active_term_filter

students_bp = Blueprint("students", __name__, url_prefix="/students")


@students_bp.route("/<int:student_id>")
@login_required
def detail(student_id):
    student = Student.query.get_or_404(student_id)

    ay, terms, selected_term = _active_term_filter()

    # All entries for this student, oldest first, optionally filtered to selected term
    q = (
        WeeklyEntry.query
        .filter_by(student_id=student_id)
        .order_by(WeeklyEntry.iso_year, WeeklyEntry.iso_week)
    )
    entries = q.all()

    if selected_term:
        entries = [e for e in entries if selected_term.contains_week(e.iso_week, e.iso_year)]

    # Ordered unique weeks
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

    lookup = {(e.subject_id, e.iso_year, e.iso_week): e.ranking for e in entries}

    entries_by_subject = defaultdict(list)
    for e in entries:
        entries_by_subject[e.subject_id].append(e)

    at_risk_subject_ids = set()
    subject_rows = []
    for subject in subjects:
        subj_entries = entries_by_subject[subject.id]
        is_risk, reason = _detect(subj_entries)
        if is_risk and reason in ("stuck", "declining"):
            at_risk_subject_ids.add(subject.id)
        history = [
            {
                "year":    year,
                "week":    week,
                "ranking": lookup.get((subject.id, year, week)),
            }
            for year, week in weeks_seen
        ]
        subject_rows.append({
            "subject":  subject,
            "history":  history,
            "is_risk":  is_risk and reason in ("stuck", "declining"),
            "reason":   reason,
            "latest":   subj_entries[-1].ranking if subj_entries else None,
        })

    # Students in same grade for dropdown + search
    grade_mates = (
        Student.query
        .filter_by(grade=student.grade)
        .order_by(Student.name)
        .all()
    )

    # All students for search fallback (across grades)
    all_students = (
        Student.query
        .order_by(Student.grade, Student.name)
        .all()
    )

    # Serialise for Alpine.js search
    grade_students_json = json.dumps([
        {"id": s.id, "name": s.name, "roll": s.roll_number, "grade": s.grade}
        for s in grade_mates
    ])
    all_students_json = json.dumps([
        {"id": s.id, "name": s.name, "roll": s.roll_number, "grade": s.grade}
        for s in all_students
    ])

    return render_template(
        "students/detail.html",
        student=student,
        weeks=weeks_seen,
        subject_rows=subject_rows,
        at_risk_subject_ids=at_risk_subject_ids,
        grade_mates=grade_mates,
        grade_students_json=grade_students_json,
        all_students_json=all_students_json,
        ranking_order=RANKING_ORDER,
        academic_year=ay,
        terms=terms,
        selected_term=selected_term,
    )
