from collections import defaultdict
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKING_ORDER

at_risk_bp = Blueprint("at_risk", __name__, url_prefix="/at-risk")


def _detect(entries: list) -> tuple[bool, str]:
    """
    Given a list of WeeklyEntry for one (student, subject) pair,
    sorted oldest → newest, return (flagged: bool, reason: str).

    Reasons (in priority order):
    - "stuck":     last 3 weeks all Working Towards.
    - "declining": downward trend ending at WT (no false positives for recoveries).
    - "improving": current week is strictly better than the previous week,
                   and student was not consistently at EE already.
    - "":          nothing notable.

    Needs at least 2 entries for improving; 3 for stuck/declining.
    """
    if len(entries) < 2:
        return False, ""

    ranks = [RANKING_ORDER[e.ranking] for e in entries]
    last2 = ranks[-2:]
    last3 = ranks[-3:] if len(ranks) >= 3 else ranks

    # --- At-risk checks (require 3 entries) ---
    if len(last3) == 3:
        # Stuck: three consecutive weeks at Working Towards
        if all(r == 1 for r in last3):
            return True, "stuck"

        # Declining (a): strict downward trend
        if last3[0] > last3[1] > last3[2]:
            return True, "declining"

        # Declining (b): current is WT but was higher at some point in the window
        if last3[-1] == 1 and any(r > 1 for r in last3[:-1]):
            return True, "declining"

    # --- Improving check (requires 2 entries) ---
    # Current ranking is strictly better than ALL prior weeks in the window
    # (genuine new high, not a return-to-baseline recovery like EE->WT->EE).
    if ranks[-1] > max(ranks[:-1]):
        return True, "improving"

    return False, ""


@at_risk_bp.route("/")
@login_required
def index():
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]
    selected_grade   = request.args.get("grade", "")
    selected_subject = request.args.get("subject", "")

    # Subject names — deduplicated so cross-grade subjects (e.g. Mathematics)
    # only appear once in the dropdown when no grade is selected.
    if selected_grade:
        subject_names = [
            r[0] for r in db.session.query(Subject.name)
            .filter_by(grade=selected_grade)
            .distinct().order_by(Subject.name).all()
        ]
    else:
        subject_names = [
            r[0] for r in db.session.query(Subject.name)
            .distinct().order_by(Subject.name).all()
        ]

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

    grouped = defaultdict(list)
    for entry in all_entries:
        grouped[(entry.student_id, entry.subject_id)].append(entry)

    rows = []

    for (student_id, subject_id), entries in grouped.items():
        is_flagged, reason = _detect(entries)
        if not is_flagged:
            continue

        last = entries[-1]
        rows.append({
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

    # Sort: declining → stuck → improving, then grade → name
    reason_order = {"declining": 0, "stuck": 1, "improving": 2}
    rows.sort(key=lambda x: (
        reason_order.get(x["reason"], 9),
        x["student"].grade,
        x["student"].name,
    ))

    counts = {r: sum(1 for x in rows if x["reason"] == r) for r in ("declining", "stuck", "improving")}

    return render_template(
        "at_risk/list.html",
        rows=rows,
        counts=counts,
        grades=grades,
        subject_names=subject_names,
        selected_grade=selected_grade,
        selected_subject=selected_subject,
    )
