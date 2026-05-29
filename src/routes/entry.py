import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required
from models import db, Student, Subject, WeeklyEntry, RANKINGS

entry_bp = Blueprint("entry", __name__, url_prefix="/entry")


def _current_week():
    today = datetime.date.today()
    iso = today.isocalendar()
    return iso.week, iso.year


def _week_label(week, year):
    try:
        monday = datetime.date.fromisocalendar(year, week, 1)
        sunday = monday + datetime.timedelta(days=6)
        return f"{monday.strftime('%d %b')} – {sunday.strftime('%d %b %Y')}"
    except ValueError:
        return f"{year}, week {week}"


def _week_options(centre_week, centre_year, past=8, future=2):
    """Return a list of {week, year, label} dicts spanning past..future weeks from centre."""
    centre = datetime.date.fromisocalendar(centre_year, centre_week, 1)
    options = []
    for delta in range(-past, future + 1):
        day = centre + datetime.timedelta(weeks=delta)
        iso = day.isocalendar()
        options.append({
            "week":  iso.week,
            "year":  iso.year,
            "label": _week_label(iso.week, iso.year),
        })
    return options


@entry_bp.route("/")
@login_required
def form():
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]
    week, year = _current_week()
    selected_grade = request.args.get("grade", grades[0] if grades else None)
    return render_template(
        "entry/weekly_form.html",
        grades=grades,
        selected_grade=selected_grade,
        rankings=RANKINGS,
        current_week=week,
        current_year=year,
        week_options=_week_options(week, year),
    )


@entry_bp.route("/students")
@login_required
def students():
    grade = request.args.get("grade", "")
    rows = Student.query.filter_by(grade=grade).order_by(Student.roll_number).all()
    return jsonify([{"id": s.id, "name": s.name, "roll_number": s.roll_number} for s in rows])


@entry_bp.route("/subjects")
@login_required
def subjects():
    grade = request.args.get("grade", "")
    rows = Subject.query.filter_by(grade=grade).order_by(Subject.name).all()
    return jsonify([{"id": s.id, "name": s.name} for s in rows])


@entry_bp.route("/", methods=["POST"])
@login_required
def submit():
    week = int(request.form.get("iso_week"))
    year = int(request.form.get("iso_year"))

    entries = {}
    for key, value in request.form.items():
        if key.startswith("ranking_") and value in RANKINGS:
            _, student_id, subject_id = key.split("_", 2)
            entries[(int(student_id), int(subject_id))] = value

    if not entries:
        flash("No rankings submitted.", "error")
        return redirect(url_for("entry.form"))

    for (student_id, subject_id), ranking in entries.items():
        existing = WeeklyEntry.query.filter_by(
            student_id=student_id,
            subject_id=subject_id,
            iso_week=week,
            iso_year=year,
        ).first()
        if existing:
            existing.ranking = ranking
        else:
            db.session.add(WeeklyEntry(
                student_id=student_id,
                subject_id=subject_id,
                iso_week=week,
                iso_year=year,
                ranking=ranking,
            ))

    db.session.commit()
    flash(f"Saved {len(entries)} ranking(s) for Week {week}, {year}.", "success")
    return redirect(url_for("entry.form"))
