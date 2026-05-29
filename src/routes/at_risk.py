from collections import defaultdict
from flask import Blueprint, render_template, request, abort
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, AcademicYear, Term, RANKING_ORDER
from permissions import perms as get_perms

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


def _active_term_filter():
    """
    Return the active AcademicYear, its terms, and the currently selected Term
    based on the `term_id` query param (defaults to the term containing today).
    Returns (academic_year | None, terms, selected_term | None).
    """
    ay = AcademicYear.query.filter_by(is_active=True).first()
    if not ay:
        return None, [], None

    terms = ay.terms  # ordered by id (Term 1 → 2 → 3)

    # Honour explicit ?term_id= param
    term_id = request.args.get("term_id", type=int)
    if term_id:
        selected = next((t for t in terms if t.id == term_id), None)
    else:
        import datetime
        today_iso = datetime.date.today().isocalendar()
        selected = next(
            (t for t in terms if t.contains_week(today_iso.week, today_iso.year)),
            terms[-1] if terms else None,  # fallback to last term
        )

    return ay, terms, selected


@at_risk_bp.route("/")
@login_required
def index():
    p = get_perms()
    all_grades = [r[0] for r in db.session.query(Student.grade).filter(Student.is_active == True).distinct().order_by(Student.grade).all()]
    vg = p.visible_grades()
    grades = all_grades if vg is None else [g for g in all_grades if g in vg]

    selected_grade   = request.args.get("grade", "")
    selected_subject = request.args.get("subject", "")

    # Guard against accessing a grade out of scope
    if selected_grade and not p.can_view_grade(selected_grade):
        abort(403)

    ay, terms, selected_term = _active_term_filter()

    # Subject IDs this user may view, per grade (None = no restriction)
    def _visible_sids_for(grade):
        return p.visible_subject_ids_for_grade(grade) if grade else None

    # Subject dropdown — restricted to the user's viewable subjects for the grade
    if selected_grade:
        sq = db.session.query(Subject.name).filter(Subject.grade == selected_grade, Subject.is_active == True)
        vsids = _visible_sids_for(selected_grade)
        if vsids is not None:
            sq = sq.filter(Subject.id.in_(vsids))
        subject_names = [r[0] for r in sq.distinct().order_by(Subject.name).all()]
    else:
        # No grade selected — show union of all subjects the user can see
        if p.can_view_all:
            subject_names = [
                r[0] for r in db.session.query(Subject.name)
                .filter(Subject.is_active == True)
                .distinct().order_by(Subject.name).all()
            ]
        else:
            ep = p.enterable_pairs() or set()
            visible_sid_list = [sid for _, sid in ep]
            subject_names = [
                r[0] for r in db.session.query(Subject.name)
                .filter(Subject.id.in_(visible_sid_list), Subject.is_active == True)
                .distinct().order_by(Subject.name).all()
            ] if visible_sid_list else []

    q = (
        WeeklyEntry.query
        .join(Student)
        .filter(Student.is_active == True)
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
        # Further restrict to subjects visible in this grade
        vsids = _visible_sids_for(selected_grade)
        if vsids is not None:
            q = q.filter(WeeklyEntry.subject_id.in_(vsids))
    elif vg is not None:
        # Restrict to visible grades
        q = q.filter(Student.grade.in_(list(vg)))
        # Also restrict to enterable subjects across those grades
        ep = p.enterable_pairs()
        if ep is not None:
            q = q.filter(WeeklyEntry.subject_id.in_([sid for _, sid in ep]))
    if selected_subject:
        q = q.join(Subject).filter(Subject.name == selected_subject)

    all_entries = q.all()

    # Filter to selected term window (if a term is active)
    if selected_term:
        all_entries = [
            e for e in all_entries
            if selected_term.contains_week(e.iso_week, e.iso_year)
        ]

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
        academic_year=ay,
        terms=terms,
        selected_term=selected_term,
    )
