import datetime
from collections import defaultdict
from flask import Blueprint, render_template, request, abort
from flask_login import login_required
from models import (
    db, Student, Subject, Rubric, FortnightEntry, AcademicYear, Term,
    RANKING_ORDER, WARNING_THRESHOLD, AT_RISK_THRESHOLD,
    date_to_fortnight,
)
from permissions import perms as get_perms

at_risk_bp = Blueprint("at_risk", __name__, url_prefix="/at-risk")


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _detect_rubric(entries: list) -> tuple[bool, str]:
    """
    Given FortnightEntry list for one (student, rubric) pair, sorted oldest→newest.
    Returns (flagged, reason) — reason is 'stuck' | 'declining' | ''.
    Requires at least 3 entries.
    """
    if len(entries) < 3:
        return False, ""

    ranks = [RANKING_ORDER[e.ranking] for e in entries]
    last3 = ranks[-3:]

    if all(r == 1 for r in last3):
        return True, "stuck"

    if last3[0] > last3[1] > last3[2]:
        return True, "declining"

    if last3[-1] == 1 and any(r > 1 for r in last3[:-1]):
        return True, "declining"

    return False, ""


def _subject_tier(stuck_rubric_count: int) -> str:
    """Return 'at_risk' | 'warning' | '' based on count of stuck rubrics."""
    if stuck_rubric_count >= AT_RISK_THRESHOLD:
        return "at_risk"
    if stuck_rubric_count >= WARNING_THRESHOLD:
        return "warning"
    return ""


# ---------------------------------------------------------------------------
# Term filter helper (shared with dashboard)
# ---------------------------------------------------------------------------

def _active_term_filter():
    """
    Return (academic_year | None, terms, selected_term | None).
    Respects ?term_id= param; defaults to the term containing today's fortnight.
    """
    ay = AcademicYear.query.filter_by(is_active=True).first()
    if not ay:
        return None, [], None

    terms = ay.terms

    term_id = request.args.get("term_id", type=int)
    if term_id:
        selected = next((t for t in terms if t.id == term_id), None)
    else:
        today = datetime.date.today()
        fy, fm, fp = date_to_fortnight(today)
        selected = next(
            (t for t in terms if t.contains_fortnight(fy, fm, fp)),
            terms[-1] if terms else None,
        )

    return ay, terms, selected


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@at_risk_bp.route("/")
@login_required
def index():
    p = get_perms()
    all_grades = [
        r[0] for r in
        db.session.query(Student.grade)
        .filter(Student.is_active == True)
        .distinct().order_by(Student.grade).all()
    ]
    vg = p.visible_grades()
    grades = all_grades if vg is None else [g for g in all_grades if g in vg]

    selected_grade   = request.args.get("grade", "")
    selected_subject = request.args.get("subject", "")
    selected_tier    = request.args.get("tier", "")   # 'at_risk' | 'warning' | ''

    if selected_grade and not p.can_view_grade(selected_grade):
        abort(403)

    ay, terms, selected_term = _active_term_filter()

    def _visible_sids_for(grade):
        return p.visible_subject_ids_for_grade(grade) if grade else None

    # Subject dropdown
    if selected_grade:
        sq = (db.session.query(Subject.name)
              .filter(Subject.grade == selected_grade, Subject.is_active == True))
        vsids = _visible_sids_for(selected_grade)
        if vsids is not None:
            sq = sq.filter(Subject.id.in_(vsids))
        subject_names = [r[0] for r in sq.distinct().order_by(Subject.name).all()]
    else:
        if p.can_view_all:
            subject_names = [
                r[0] for r in db.session.query(Subject.name)
                .filter(Subject.is_active == True)
                .distinct().order_by(Subject.name).all()
            ]
        else:
            ep = p.enterable_pairs() or set()
            visible_sid_list = [sid for _, sid in ep]
            subject_names = (
                [r[0] for r in db.session.query(Subject.name)
                 .filter(Subject.id.in_(visible_sid_list), Subject.is_active == True)
                 .distinct().order_by(Subject.name).all()]
                if visible_sid_list else []
            )

    # Pull all entries (rubric-level)
    q = (
        FortnightEntry.query
        .join(Student)
        .filter(Student.is_active == True)
        .order_by(
            Student.grade,
            FortnightEntry.student_id,
            FortnightEntry.subject_id,
            FortnightEntry.rubric_id,
            FortnightEntry.ft_year,
            FortnightEntry.ft_month,
            FortnightEntry.ft_period,
        )
    )
    if selected_grade:
        q = q.filter(Student.grade == selected_grade)
        vsids = _visible_sids_for(selected_grade)
        if vsids is not None:
            q = q.filter(FortnightEntry.subject_id.in_(vsids))
    elif vg is not None:
        q = q.filter(Student.grade.in_(list(vg)))
        ep = p.enterable_pairs()
        if ep is not None:
            q = q.filter(FortnightEntry.subject_id.in_([sid for _, sid in ep]))
    if selected_subject:
        q = q.join(Subject).filter(Subject.name == selected_subject)

    all_entries = q.all()

    # Filter to selected term
    if selected_term:
        all_entries = [
            e for e in all_entries
            if selected_term.contains_fortnight(e.ft_year, e.ft_month, e.ft_period)
        ]

    # Group by (student, subject, rubric)
    by_rubric: dict = defaultdict(list)
    for e in all_entries:
        by_rubric[(e.student_id, e.subject_id, e.rubric_id)].append(e)

    # Detect stuck/declining per rubric
    # Then aggregate to subject level → tier
    # Structure: { (student_id, subject_id) → { rubric_id → (flagged, reason, entries) } }
    by_subject: dict = defaultdict(dict)
    for (student_id, subject_id, rubric_id), entries in by_rubric.items():
        flagged, reason = _detect_rubric(entries)
        if flagged:
            by_subject[(student_id, subject_id)][rubric_id] = {
                "reason":  reason,
                "entries": entries,
            }

    # Build rows at subject level
    rows = []
    for (student_id, subject_id), rubric_flags in by_subject.items():
        stuck_count = len(rubric_flags)
        tier = _subject_tier(stuck_count)
        if not tier:
            continue

        # Pick any entry to get student/subject objects
        sample_entries = next(iter(rubric_flags.values()))["entries"]
        last_entry = sample_entries[-1]

        # Build per-rubric detail rows (last 3 fortnights each)
        rubric_rows = []
        for rubric_id, rdata in rubric_flags.items():
            rentry_last = rdata["entries"][-1]
            rubric_rows.append({
                "rubric":    rentry_last.rubric,
                "reason":    rdata["reason"],
                "ranking":   rentry_last.ranking,
                "history":   [
                    {
                        "year":    e.ft_year,
                        "month":   e.ft_month,
                        "period":  e.ft_period,
                        "ranking": e.ranking,
                    }
                    for e in rdata["entries"][-3:]
                ],
            })

        rows.append({
            "student":     last_entry.student,
            "subject":     last_entry.subject,
            "tier":        tier,
            "stuck_count": stuck_count,
            "rubric_rows": rubric_rows,
        })

    # Apply tier filter
    if selected_tier in ("at_risk", "warning"):
        rows = [r for r in rows if r["tier"] == selected_tier]

    # Sort: at_risk first, then warning; within each tier grade → name → subject
    tier_order = {"at_risk": 0, "warning": 1}
    rows.sort(key=lambda x: (
        tier_order.get(x["tier"], 9),
        x["student"].grade,
        x["student"].name,
        x["subject"].name,
    ))

    counts = {
        "at_risk": sum(1 for r in rows if r["tier"] == "at_risk"),
        "warning": sum(1 for r in rows if r["tier"] == "warning"),
    }

    # Group by student for the card view
    seen = {}
    grouped_rows = []
    for row in rows:
        sid = row["student"].id
        if sid not in seen:
            seen[sid] = {
                "student":  row["student"],
                "worst":    row["tier"],
                "subjects": [],
            }
            grouped_rows.append(seen[sid])
        # Escalate worst tier if needed
        if row["tier"] == "at_risk":
            seen[sid]["worst"] = "at_risk"
        seen[sid]["subjects"].append(row)

    return render_template(
        "at_risk/list.html",
        rows=rows,
        grouped_rows=grouped_rows,
        counts=counts,
        grades=grades,
        subject_names=subject_names,
        selected_grade=selected_grade,
        selected_subject=selected_subject,
        selected_tier=selected_tier,
        academic_year=ay,
        terms=terms,
        selected_term=selected_term,
    )
