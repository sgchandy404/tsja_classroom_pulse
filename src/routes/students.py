import json
from collections import defaultdict
from flask import Blueprint, render_template, request, abort
from flask_login import login_required
from models import (
    db, Student, Subject, Rubric, FortnightEntry, AcademicYear, Term,
    RANKING_ORDER, fortnight_label,
)
from routes.at_risk import _detect_rubric, _subject_tier, _active_term_filter
from permissions import perms as get_perms

students_bp = Blueprint("students", __name__, url_prefix="/students")


@students_bp.route("/<int:student_id>")
@login_required
def detail(student_id):
    student = Student.query.get_or_404(student_id)

    p = get_perms()
    if not p.can_view_grade(student.grade):
        abort(403)

    ay, terms, selected_term = _active_term_filter()

    visible_sids = p.visible_subject_ids_for_grade(student.grade)
    subj_q = Subject.query.filter_by(grade=student.grade, is_active=True)
    if visible_sids is not None:
        subj_q = subj_q.filter(Subject.id.in_(visible_sids))
    subjects = subj_q.order_by(Subject.name).all()

    # All entries for this student, oldest first
    q = (
        FortnightEntry.query
        .filter_by(student_id=student_id)
        .order_by(
            FortnightEntry.ft_year,
            FortnightEntry.ft_month,
            FortnightEntry.ft_period,
        )
    )
    entries = q.all()

    if selected_term:
        entries = [
            e for e in entries
            if selected_term.contains_fortnight(e.ft_year, e.ft_month, e.ft_period)
        ]

    if visible_sids is not None:
        entries = [e for e in entries if e.subject_id in visible_sids]

    # Ordered unique fortnights
    periods_seen = []
    periods_set = set()
    for e in entries:
        key = (e.ft_year, e.ft_month, e.ft_period)
        if key not in periods_set:
            periods_seen.append(key)
            periods_set.add(key)

    # lookup[(subject_id, rubric_id, year, month, period)] = ranking
    lookup = {
        (e.subject_id, e.rubric_id, e.ft_year, e.ft_month, e.ft_period): e.ranking
        for e in entries
    }

    entries_by_rubric = defaultdict(list)
    for e in entries:
        entries_by_rubric[(e.subject_id, e.rubric_id)].append(e)

    subject_rows = []
    for subject in subjects:
        rubric_rows = []
        stuck_count = 0
        for rubric in subject.rubrics:
            if not rubric.is_active:
                continue
            rubric_entries = entries_by_rubric[(subject.id, rubric.id)]
            flagged, reason = _detect_rubric(rubric_entries)
            if flagged and reason in ("stuck", "declining"):
                stuck_count += 1
            history = [
                {
                    "year":    fy,
                    "month":   fm,
                    "period":  fp,
                    "label":   fortnight_label(fy, fm, fp),
                    "ranking": lookup.get((subject.id, rubric.id, fy, fm, fp)),
                }
                for fy, fm, fp in periods_seen
            ]
            rubric_rows.append({
                "rubric":      rubric,
                "history":     history,
                "is_flagged":  flagged and reason in ("stuck", "declining"),
                "reason":      reason,
                "latest":      rubric_entries[-1].ranking if rubric_entries else None,
            })

        tier = _subject_tier(stuck_count)
        subject_rows.append({
            "subject":     subject,
            "tier":        tier,
            "stuck_count": stuck_count,
            "rubric_rows": rubric_rows,
            "latest":      rubric_rows[-1]["latest"] if rubric_rows else None,
        })

    grade_mates = (
        Student.query
        .filter_by(grade=student.grade, is_active=True)
        .order_by(Student.name).all()
    )
    all_students = (
        Student.query
        .filter(Student.is_active == True)
        .order_by(Student.grade, Student.name).all()
    )

    grade_students_json = json.dumps([
        {"id": s.id, "name": s.name, "roll": s.roll_number, "grade": s.grade}
        for s in grade_mates
    ])
    all_students_json = json.dumps([
        {"id": s.id, "name": s.name, "roll": s.roll_number, "grade": s.grade}
        for s in all_students
    ])

    period_labels = [fortnight_label(y, m, p) for y, m, p in periods_seen]

    return render_template(
        "students/detail.html",
        student=student,
        periods=periods_seen,
        period_labels=period_labels,
        subject_rows=subject_rows,
        grade_mates=grade_mates,
        grade_students_json=grade_students_json,
        all_students_json=all_students_json,
        ranking_order=RANKING_ORDER,
        academic_year=ay,
        terms=terms,
        selected_term=selected_term,
    )
