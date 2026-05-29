import datetime
from collections import defaultdict
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKINGS

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _current_week():
    iso = datetime.date.today().isocalendar()
    return iso.week, iso.year


def _week_label(week, year):
    try:
        monday = datetime.date.fromisocalendar(year, week, 1)
        sunday = monday + datetime.timedelta(days=6)
        return f"Wk {week} · {monday.strftime('%d %b')} – {sunday.strftime('%d %b %Y')}"
    except ValueError:
        return f"Week {week}, {year}"


@dashboard_bp.route("/")
@login_required
def index():
    # Available grades and weeks for filters
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]

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
        subjects = Subject.query.filter_by(grade=selected_grade).order_by(Subject.name).all()
        student_count = Student.query.filter_by(grade=selected_grade).count()

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
        entries = (
            WeeklyEntry.query
            .join(Student)
            .filter(
                Student.grade == grade,
                WeeklyEntry.iso_week == selected_week,
                WeeklyEntry.iso_year == selected_year,
            ).all()
        )
        counts = defaultdict(int)
        for e in entries:
            counts[e.ranking] += 1
        total = sum(counts.values())
        grade_summaries.append({
            "grade": grade,
            "total": total,
            "counts": {r: counts[r] for r in RANKINGS},
        })

    return render_template(
        "dashboard/index.html",
        grades=grades,
        selected_grade=selected_grade,
        selected_week=selected_week,
        selected_year=selected_year,
        available_weeks=available_weeks,
        week_label=_week_label(selected_week, selected_year),
        breakdown=breakdown,
        grade_summaries=grade_summaries,
        rankings=RANKINGS,
    )
