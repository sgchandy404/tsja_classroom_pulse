import datetime
import json
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, abort
from flask_login import login_required
from models import (
    db, Student, Subject, Rubric, FortnightEntry, RANKINGS, RANKING_ORDER,
    date_to_fortnight, fortnight_label, prev_fortnight, next_fortnight,
)
from permissions import perms as get_perms
from routes.at_risk import _subject_tier, _detect_rubric, _active_term_filter

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _student_counts(entries):
    """Return (counts_by_ranking, n_students) where each student contributes
    exactly once, bucketed by their worst rubric ranking for that set of entries."""
    by_student = defaultdict(list)
    for e in entries:
        by_student[e.student_id].append(e.ranking)
    counts = defaultdict(int)
    for rnks in by_student.values():
        worst = min(rnks, key=lambda r: RANKING_ORDER[r])
        counts[worst] += 1
    return counts, len(by_student)


@dashboard_bp.route("/")
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

    requested_grade = request.args.get("grade", "")
    if requested_grade and not p.can_view_grade(requested_grade):
        abort(403)

    # Resolve current fortnight
    today = datetime.date.today()
    cur_year, cur_month, cur_period = date_to_fortnight(today)

    selected_grade  = request.args.get("grade", grades[0] if grades else None)
    selected_year   = int(request.args.get("year",   cur_year))
    selected_month  = int(request.args.get("month",  cur_month))
    selected_period = int(request.args.get("period", cur_period))

    # Previous / next fortnight for nav arrows
    prev_y, prev_m, prev_p = prev_fortnight(selected_year, selected_month, selected_period)
    next_y, next_m, next_p = next_fortnight(selected_year, selected_month, selected_period)

    current_label = fortnight_label(selected_year, selected_month, selected_period)
    prev_label    = fortnight_label(prev_y, prev_m, prev_p)

    # Collect available fortnight periods that have data
    ft_rows = (
        db.session.query(
            FortnightEntry.ft_year,
            FortnightEntry.ft_month,
            FortnightEntry.ft_period,
        )
        .distinct()
        .order_by(
            FortnightEntry.ft_year.desc(),
            FortnightEntry.ft_month.desc(),
            FortnightEntry.ft_period.desc(),
        )
        .all()
    )
    available_periods = [
        {
            "year": r.ft_year, "month": r.ft_month, "period": r.ft_period,
            "label": fortnight_label(r.ft_year, r.ft_month, r.ft_period),
        }
        for r in ft_rows
    ]

    # Per-subject breakdown for selected grade + fortnight
    breakdown = []
    subjects = []
    if selected_grade:
        subj_q = Subject.query.filter_by(grade=selected_grade, is_active=True)
        visible_sids = p.visible_subject_ids_for_grade(selected_grade)
        if visible_sids is not None:
            subj_q = subj_q.filter(Subject.id.in_(visible_sids))
        subjects = subj_q.order_by(Subject.name).all()
        student_count = Student.query.filter_by(grade=selected_grade, is_active=True).count()

        for subject in subjects:
            entries = FortnightEntry.query.filter_by(
                subject_id=subject.id,
                ft_year=selected_year, ft_month=selected_month, ft_period=selected_period,
            ).all()

            counts, entered_count = _student_counts(entries)

            breakdown.append({
                "subject":       subject.name,
                "entered_count": entered_count,
                "student_count": student_count,
                "counts":        {r: counts[r] for r in RANKINGS},
                "pct":           {
                    r: round(counts[r] / entered_count * 100) if entered_count else 0
                    for r in RANKINGS
                },
            })

    # Grade-level summary cards
    grade_summaries = []
    for grade in grades:
        gq = (
            FortnightEntry.query
            .join(Student)
            .filter(
                Student.grade == grade,
                Student.is_active == True,
                FortnightEntry.ft_year   == selected_year,
                FortnightEntry.ft_month  == selected_month,
                FortnightEntry.ft_period == selected_period,
            )
        )
        gsids = p.visible_subject_ids_for_grade(grade)
        if gsids is not None:
            gq = gq.filter(FortnightEntry.subject_id.in_(gsids))
        entries = gq.all()
        counts, n_students = _student_counts(entries)
        grade_summaries.append({
            "grade":  grade,
            "total":  n_students,
            "counts": {r: counts[r] for r in RANKINGS},
        })

    # At-risk summary (current term, visible grades) — tiered Warning / At-Risk
    _, _, active_term = _active_term_filter()
    ar_q = (
        FortnightEntry.query
        .join(Student)
        .filter(Student.is_active == True)
        .order_by(
            FortnightEntry.student_id,
            FortnightEntry.subject_id,
            FortnightEntry.rubric_id,
            FortnightEntry.ft_year,
            FortnightEntry.ft_month,
            FortnightEntry.ft_period,
        )
    )
    if vg is not None:
        ar_q = ar_q.filter(Student.grade.in_(list(vg)))
    ar_entries = ar_q.all()
    if active_term:
        ar_entries = [
            e for e in ar_entries
            if active_term.contains_fortnight(e.ft_year, e.ft_month, e.ft_period)
        ]
    # Only count entries up to the selected fortnight so the banner reflects
    # the at-risk picture as of the period being viewed.
    ar_entries = [
        e for e in ar_entries
        if (e.ft_year, e.ft_month, e.ft_period) <= (selected_year, selected_month, selected_period)
    ]

    by_rubric = defaultdict(list)
    for e in ar_entries:
        by_rubric[(e.student_id, e.subject_id, e.rubric_id)].append(e)

    by_subject = defaultdict(int)
    sid_to_grade = {}
    for (s_id, subj_id, _), es in by_rubric.items():
        flagged, _ = _detect_rubric(es)
        if flagged:
            by_subject[(s_id, subj_id)] += 1
        if s_id not in sid_to_grade and es:
            sid_to_grade[s_id] = es[0].student.grade

    at_risk_counts = {"at_risk": 0, "warning": 0,
                      "at_risk_students": 0, "warning_students": 0}
    grade_at_risk = defaultdict(int)
    grade_warning = defaultdict(int)
    student_worst = {}

    for (s_id, subj_id), stuck_ct in by_subject.items():
        tier = _subject_tier(stuck_ct)
        if not tier:
            continue
        at_risk_counts[tier] += 1
        prev = student_worst.get(s_id, "")
        if tier == "at_risk" or (tier == "warning" and prev != "at_risk"):
            student_worst[s_id] = tier

    for s_id, worst in student_worst.items():
        grade = sid_to_grade.get(s_id, "")
        if worst == "at_risk":
            at_risk_counts["at_risk_students"] += 1
            grade_at_risk[grade] += 1
        elif worst == "warning":
            at_risk_counts["warning_students"] += 1
            grade_warning[grade] += 1

    # Prev fortnight per-grade ME+EE% for trend arrows
    prev_grade_pct = {}
    for grade in grades:
        pg_q = (
            FortnightEntry.query.join(Student)
            .filter(
                Student.grade == grade, Student.is_active == True,
                FortnightEntry.ft_year   == prev_y,
                FortnightEntry.ft_month  == prev_m,
                FortnightEntry.ft_period == prev_p,
            )
        )
        pg_sids = p.visible_subject_ids_for_grade(grade)
        if pg_sids is not None:
            pg_q = pg_q.filter(FortnightEntry.subject_id.in_(pg_sids))
        pg_counts, pg_total = _student_counts(pg_q.all())
        if pg_total:
            prev_grade_pct[grade] = round(
                (pg_counts["Meets Expectations"] + pg_counts["Exceeds Expectations"])
                / pg_total * 100
            )

    # Trend data: per-subject ME+EE% across all available fortnights (chronological)
    trend_periods = [
        ap for ap in reversed(available_periods)
        if (ap["year"], ap["month"], ap["period"]) <= (selected_year, selected_month, selected_period)
    ]
    trend_labels  = [ap["label"] for ap in trend_periods]

    # Term-only subset
    if active_term:
        term_indices = [i for i, ap in enumerate(trend_periods)
                        if active_term.contains_fortnight(ap["year"], ap["month"], ap["period"])]
    else:
        term_indices = list(range(len(trend_periods)))
    trend_labels_term = [trend_labels[i] for i in term_indices]

    trend_data = []
    if selected_grade and subjects:
        for subject in subjects:
            pcts = []
            for ap in trend_periods:
                es = FortnightEntry.query.filter_by(
                    subject_id=subject.id,
                    ft_year=ap["year"], ft_month=ap["month"], ft_period=ap["period"],
                ).all()
                cnt, n = _student_counts(es)
                pct = round((cnt["Meets Expectations"] + cnt["Exceeds Expectations"]) / n * 100) if n else None
                pcts.append(pct)
            trend_data.append({
                "subject":   subject.name,
                "data":      pcts,
                "term_data": [pcts[i] for i in term_indices],
            })

    breakdown_json = json.dumps({
        "subjects": [r["subject"] for r in breakdown],
        "student_count": [r["student_count"] for r in breakdown],
        "wt":   [r["pct"]["Working Towards"]      for r in breakdown],
        "me":   [r["pct"]["Meets Expectations"]   for r in breakdown],
        "ee":   [r["pct"]["Exceeds Expectations"] for r in breakdown],
        "wt_n": [r["counts"]["Working Towards"]      for r in breakdown],
        "me_n": [r["counts"]["Meets Expectations"]   for r in breakdown],
        "ee_n": [r["counts"]["Exceeds Expectations"] for r in breakdown],
    })

    return render_template(
        "dashboard/index.html",
        grades=grades,
        selected_grade=selected_grade,
        selected_year=selected_year,
        selected_month=selected_month,
        selected_period=selected_period,
        current_label=current_label,
        prev_label=prev_label,
        prev_y=prev_y, prev_m=prev_m, prev_p=prev_p,
        next_y=next_y, next_m=next_m, next_p=next_p,
        available_periods=available_periods,
        breakdown=breakdown,
        grade_summaries=grade_summaries,
        rankings=RANKINGS,
        at_risk_counts=at_risk_counts,
        active_term=active_term,
        grade_at_risk=grade_at_risk,
        grade_warning=grade_warning,
        prev_grade_pct=prev_grade_pct,
        breakdown_json=breakdown_json,
        trend_data=trend_data,
        trend_labels=trend_labels,
        trend_labels_term=trend_labels_term,
    )
