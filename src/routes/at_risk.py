from collections import defaultdict
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKING_ORDER

at_risk_bp = Blueprint("at_risk", __name__, url_prefix="/at-risk")


def _detect(entries: list) -> tuple[bool, str]:
    """
    Given a list of WeeklyEntry for one (student, subject) pair,
    sorted oldest → newest, return (is_at_risk, reason).
    Needs at least 3 entries to flag.

    Rules (evaluated on last 3 entries):
    - Stuck:     all 3 weeks are Working Towards (rank 1).
    - Declining: (a) strict downward trend across all 3 weeks, OR
                 (b) current week is Working Towards and at least one of
                     the prior 2 weeks was higher — catches patterns like
                     EE→WT→WT and ME→EE→WT without false-flagging
                     recoveries like EE→WT→EE.
    """
    if len(entries) < 3:
        return False, ""

    last3 = entries[-3:]
    ranks = [RANKING_ORDER[e.ranking] for e in last3]

    # Stuck: three consecutive weeks at Working Towards
    if all(r == 1 for r in ranks):
        return True, "stuck"

    # Declining (a): strict downward trend — each week lower than the last
    if ranks[0] > ranks[1] > ranks[2]:
        return True, "declining"

    # Declining (b): current week is WT but wasn't always WT in the window
    # (student slipped to the lowest ranking; excludes recoveries where
    #  current ranking is above WT)
    if ranks[-1] == 1 and any(r > 1 for r in ranks[:-1]):
        return True, "declining"

    return False, ""


@at_risk_bp.route("/")
@login_required
def index():
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]
    selected_grade   = request.args.get("grade", "")
    selected_subject = request.args.get("subject", "")

    # Subjects list — filtered by grade if one is selected
    subj_q = Subject.query.order_by(Subject.name)
    if selected_grade:
        subj_q = subj_q.filter_by(grade=selected_grade)
    subjects = subj_q.all()

    q = (
        WeeklyEntry.query
        .join(Student)
        .order_by(
            Student.grade,
            WeeklyEntry.student_id,
            WeeklyEntry.subject_id,
            WeeklyEntry.iso_year,
            WeeklyEntry.iso_week,
        )
    )
    if selected_grade:
        q = q.filter(Student.grade == selected_grade)
    if selected_subject:
        q = q.join(Subject).filter(Subject.name == selected_subject)

    all_entries = q.all()

    # Group by (student_id, subject_id)
    grouped = defaultdict(list)
    for entry in all_entries:
        grouped[(entry.student_id, entry.subject_id)].append(entry)

    flagged = []
    for (student_id, subject_id), entries in grouped.items():
        is_risk, reason = _detect(entries)
        if is_risk:
            last = entries[-1]
            flagged.append({
                "student":      last.student,
                "subject":      last.subject,
                "reason":       reason,
                "last_ranking": last.ranking,
                "last_week":    last.iso_week,
                "last_year":    last.iso_year,
                "weeks_data":   [
                    {"week": e.iso_week, "year": e.iso_year, "ranking": e.ranking}
                    for e in entries[-3:]
                ],
            })

    flagged.sort(key=lambda x: (
        0 if x["reason"] == "declining" else 1,
        x["student"].grade,
        x["student"].name,
    ))

    return render_template(
        "at_risk/list.html",
        flagged=flagged,
        grades=grades,
        subjects=subjects,
        selected_grade=selected_grade,
        selected_subject=selected_subject,
    )
