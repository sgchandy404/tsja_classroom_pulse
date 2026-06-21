import datetime
import json
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, abort
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKINGS
from permissions import perms as get_perms
from routes.at_risk import _detect, _active_term_filter

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _current_week():
    iso = datetime.date.today().isocalendar()
    return iso.week, iso.year


def _week_label(week, year):
    try:
        monday = datetime.date.fromisocalendar(year, week, 1)
        sunday = monday + datetime.timedelta(days=6)
        return f"{monday.strftime('%d/%m/%Y')} – {sunday.strftime('%d/%m/%Y')}"
    except ValueError:
        return f"Week {week}, {year}"


def _adjacent_week(week, year, delta):
    """Return (week, year) shifted by delta weeks (+1 or -1)."""
    monday = datetime.date.fromisocalendar(year, week, 1)
    target = monday + datetime.timedelta(weeks=delta)
    iso = target.isocalendar()
    return iso.week, iso.year


MONTHS = [
    (1, "January"), (2, "February"), (3, "March"), (4, "April"),
    (5, "May"), (6, "June"), (7, "July"), (8, "August"),
    (9, "September"), (10, "October"), (11, "November"), (12, "December"),
]


@dashboard_bp.route("/")
@login_required
def index():
    # Month+year jump: redirect to the ISO week of the 1st of that month
    if request.args.get("month") and request.args.get("jump_year"):
        try:
            month     = int(request.args["month"])
            jump_year = int(request.args["jump_year"])
            first_day = datetime.date(jump_year, month, 1)
            iso       = first_day.isocalendar()
            return redirect(url_for(
                "dashboard.index",
                grade=request.args.get("grade", ""),
                week=iso.week,
                year=iso.year,
            ))
        except (ValueError, TypeError):
            pass

    # Available grades — filter by permission
    p = get_perms()
    all_grades = [r[0] for r in db.session.query(Student.grade).filter(Student.is_active == True).distinct().order_by(Student.grade).all()]
    vg = p.visible_grades()
    grades = all_grades if vg is None else [g for g in all_grades if g in vg]

    # If a specific grade is requested but not visible, 403
    requested_grade = request.args.get("grade", "")
    if requested_grade and not p.can_view_grade(requested_grade):
        abort(403)

    week_rows = (
        db.session.query(WeeklyEntry.iso_week, WeeklyEntry.iso_year)
        .distinct()
        .order_by(WeeklyEntry.iso_year.desc(), WeeklyEntry.iso_week.desc())
        .all()
    )
    available_weeks = [{"week": r.iso_week, "year": r.iso_year,
                        "label": _week_label(r.iso_week, r.iso_year)} for r in week_rows]

    current_week, current_year = _current_week()
    selected_grade = request.args.get("grade", grades[0] if grades else None)
    selected_week  = int(request.args.get("week", current_week))
    selected_year  = int(request.args.get("year", current_year))

    # Pull entries for selected grade + week
    breakdown = []
    if selected_grade:
        subj_q = Subject.query.filter_by(grade=selected_grade).filter(Subject.is_active == True)
        visible_sids = p.visible_subject_ids_for_grade(selected_grade)
        if visible_sids is not None:
            subj_q = subj_q.filter(Subject.id.in_(visible_sids))
        subjects = subj_q.order_by(Subject.name).all()
        student_count = Student.query.filter_by(grade=selected_grade, is_active=True).count()

        for subject in subjects:
            entries = WeeklyEntry.query.filter_by(
                subject_id=subject.id,
                iso_week=selected_week,
                iso_year=selected_year,
            ).all()

            counts = defaultdict(int)
            for e in entries:
                counts[e.ranking] += 1
            total = sum(counts.values())

            breakdown.append({
                "subject": subject.name,
                "total": total,
                "student_count": student_count,
                "counts": {r: counts[r] for r in RANKINGS},
                "pct": {
                    r: round(counts[r] / total * 100) if total else 0
                    for r in RANKINGS
                },
            })

    # Grade-level summary cards (all grades, current week)
    grade_summaries = []
    for grade in grades:
        gq = (
            WeeklyEntry.query
            .join(Student)
            .filter(
                Student.grade == grade,
                Student.is_active == True,
                WeeklyEntry.iso_week == selected_week,
                WeeklyEntry.iso_year == selected_year,
            )
        )
        gsids = p.visible_subject_ids_for_grade(grade)
        if gsids is not None:
            gq = gq.filter(WeeklyEntry.subject_id.in_(gsids))
        entries = gq.all()
        counts = defaultdict(int)
        for e in entries:
            counts[e.ranking] += 1
        total = sum(counts.values())
        grade_summaries.append({
            "grade": grade,
            "total": total,
            "counts": {r: counts[r] for r in RANKINGS},
        })

    # At-risk summary for hero card (current term, visible grades only)
    _, _, active_term = _active_term_filter()
    ar_q = (
        WeeklyEntry.query
        .join(Student)
        .filter(Student.is_active == True)
        .order_by(WeeklyEntry.student_id, WeeklyEntry.subject_id,
                  WeeklyEntry.iso_year, WeeklyEntry.iso_week)
    )
    if vg is not None:
        ar_q = ar_q.filter(Student.grade.in_(list(vg)))
    ar_entries = ar_q.all()
    if active_term:
        ar_entries = [e for e in ar_entries if active_term.contains_week(e.iso_week, e.iso_year)]
    ar_grouped = defaultdict(list)
    sid_to_grade = {}
    for e in ar_entries:
        ar_grouped[(e.student_id, e.subject_id)].append(e)
        if e.student_id not in sid_to_grade:
            sid_to_grade[e.student_id] = e.student.grade
    at_risk_counts = {"declining": 0, "stuck": 0, "improving": 0, "total_students": 0}
    at_risk_sids = set()
    grade_at_risk_sids = defaultdict(set)
    for (s_id, _), es in ar_grouped.items():
        flagged, reason = _detect(es)
        if not flagged:
            continue
        at_risk_counts[reason] = at_risk_counts.get(reason, 0) + 1
        if reason in ("declining", "stuck"):
            at_risk_sids.add(s_id)
            grade_at_risk_sids[sid_to_grade.get(s_id, "")].add(s_id)
    at_risk_counts["total_students"] = len(at_risk_sids)
    grade_at_risk = {g: len(grade_at_risk_sids[g]) for g in grades}

    prev_week, prev_year = _adjacent_week(selected_week, selected_year, -1)
    next_week, next_year = _adjacent_week(selected_week, selected_year, +1)

    # Prev week per-grade ME+EE % for trend arrows on grade cards
    prev_grade_pct = {}
    for grade in grades:
        pg_q = (WeeklyEntry.query.join(Student)
                .filter(Student.grade == grade, Student.is_active == True,
                        WeeklyEntry.iso_week == prev_week, WeeklyEntry.iso_year == prev_year))
        pg_sids = p.visible_subject_ids_for_grade(grade)
        if pg_sids is not None:
            pg_q = pg_q.filter(WeeklyEntry.subject_id.in_(pg_sids))
        pg_entries = pg_q.all()
        pg_counts = defaultdict(int)
        for e in pg_entries:
            pg_counts[e.ranking] += 1
        pg_total = sum(pg_counts.values())
        if pg_total:
            prev_grade_pct[grade] = round(
                (pg_counts["Meets Expectations"] + pg_counts["Exceeds Expectations"]) / pg_total * 100
            )

    # Derive current month from the Monday of the selected week
    try:
        selected_month = datetime.date.fromisocalendar(selected_year, selected_week, 1).month
    except ValueError:
        selected_month = datetime.date.today().month

    today = datetime.date.today()
    year_range = list(range(2023, today.year + 2))

    # Restructured breakdown JSON for ApexCharts (percentages + raw counts)
    breakdown_json = json.dumps({
        "subjects": [r["subject"] for r in breakdown],
        "coverage": [f"{r['total']}/{r['student_count']}" for r in breakdown],
        "wt":   [r["pct"]["Working Towards"]     for r in breakdown],
        "me":   [r["pct"]["Meets Expectations"]  for r in breakdown],
        "ee":   [r["pct"]["Exceeds Expectations"] for r in breakdown],
        "wt_n": [r["counts"]["Working Towards"]     for r in breakdown],
        "me_n": [r["counts"]["Meets Expectations"]  for r in breakdown],
        "ee_n": [r["counts"]["Exceeds Expectations"] for r in breakdown],
    })

    # Slope chart: subject-level ME+EE movement vs prev week
    slope_data = []
    slope_json = json.dumps([])
    if selected_grade and breakdown:
        slope_rows = []
        for subject in subjects:
            prev_e = WeeklyEntry.query.filter_by(
                subject_id=subject.id, iso_week=prev_week, iso_year=prev_year,
            ).all()
            prev_cnt = defaultdict(int)
            for e in prev_e:
                prev_cnt[e.ranking] += 1
            prev_tot = sum(prev_cnt.values())
            slope_rows.append(
                round((prev_cnt["Meets Expectations"] + prev_cnt["Exceeds Expectations"]) / prev_tot * 100)
                if prev_tot else 0
            )
        slope_data = []
        for row, prev_pct in zip(breakdown, slope_rows):
            this_pct = (
                round((row["counts"]["Meets Expectations"] + row["counts"]["Exceeds Expectations"]) / row["total"] * 100)
                if row["total"] else 0
            )
            delta = this_pct - prev_pct
            slope_data.append({
                "subject":  row["subject"],
                "last_pct": prev_pct,
                "this_pct": this_pct,
                "delta":    delta,
                "color":    "#3D9E7A" if delta > 0 else "#E07060" if delta < 0 else "#94a3b8",
            })
        slope_json = json.dumps(slope_data)  # kept for potential future JS use

    return render_template(
        "dashboard/index.html",
        grades=grades,
        selected_grade=selected_grade,
        selected_week=selected_week,
        selected_year=selected_year,
        selected_month=selected_month,
        week_label=_week_label(selected_week, selected_year),
        prev_week=prev_week, prev_year=prev_year,
        next_week=next_week, next_year=next_year,
        breakdown=breakdown,
        grade_summaries=grade_summaries,
        rankings=RANKINGS,
        months=MONTHS,
        year_range=year_range,
        at_risk_counts=at_risk_counts,
        active_term=active_term,
        grade_at_risk=grade_at_risk,
        prev_grade_pct=prev_grade_pct,
        breakdown_json=breakdown_json,
        slope_data=slope_data,
        slope_json=slope_json,
    )
