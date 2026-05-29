import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Student, Subject, WeeklyEntry, RANKINGS
from permissions import perms as get_perms, log_audit

entry_bp = Blueprint("entry", __name__, url_prefix="/entry")


def _current_week():
    today = datetime.date.today()
    iso = today.isocalendar()
    return iso.week, iso.year


def _week_label(week, year):
    try:
        monday = datetime.date.fromisocalendar(year, week, 1)
        sunday = monday + datetime.timedelta(days=6)
        return f"{monday.strftime('%d/%m/%Y')} – {sunday.strftime('%d/%m/%Y')}"
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
    p = get_perms()
    all_grades = [r[0] for r in db.session.query(Student.grade).filter(Student.is_active == True).distinct().order_by(Student.grade).all()]

    # Filter grades to what the user can enter
    enterable = p.enterable_grades()
    if enterable is None:
        grades = all_grades  # admin — no restriction
    else:
        grades = [g for g in all_grades if g in enterable]

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
    p = get_perms()
    if not p.can_view_grade(grade):
        return jsonify([])
    rows = Student.query.filter_by(grade=grade, is_active=True).order_by(Student.roll_number).all()
    return jsonify([{"id": s.id, "name": s.name, "roll_number": s.roll_number} for s in rows])


@entry_bp.route("/subjects")
@login_required
def subjects():
    grade = request.args.get("grade", "")
    p = get_perms()
    q = Subject.query.filter_by(grade=grade, is_active=True).order_by(Subject.name)
    rows = q.all()

    # Filter to subjects the user can enter
    ep = p.enterable_pairs()
    if ep is not None:
        rows = [s for s in rows if (grade, s.id) in ep]

    return jsonify([{"id": s.id, "name": s.name} for s in rows])


@entry_bp.route("/", methods=["POST"])
@login_required
def submit():
    week = int(request.form.get("iso_week"))
    year = int(request.form.get("iso_year"))
    p = get_perms()

    raw_entries = {}
    for key, value in request.form.items():
        if key.startswith("ranking_") and value in RANKINGS:
            _, student_id, subject_id = key.split("_", 2)
            raw_entries[(int(student_id), int(subject_id))] = value

    if not raw_entries:
        flash("No rankings submitted.", "error")
        return redirect(url_for("entry.form"))

    saved = 0
    locked = 0
    denied = 0
    now = datetime.datetime.utcnow()

    for (student_id, subject_id), ranking in raw_entries.items():
        subject = db.session.get(Subject, subject_id)
        if not subject:
            continue

        # Permission check
        if not p.can_enter(subject.grade, subject_id):
            denied += 1
            continue

        existing = WeeklyEntry.query.filter_by(
            student_id=student_id,
            subject_id=subject_id,
            iso_week=week,
            iso_year=year,
        ).first()

        if existing:
            if not p.can_edit_entry(existing):
                locked += 1
                continue
            old_ranking = existing.ranking
            existing.ranking   = ranking
            existing.updated_by = current_user.id
            existing.updated_at = now
            log_audit(
                user       = current_user,
                action     = "edit",
                model_name = "WeeklyEntry",
                record_id  = existing.id,
                field_name = "ranking",
                old_value  = old_ranking,
                new_value  = ranking,
                note       = "Admin override" if p.is_admin and not p.within_grace_period(existing) else None,
            )
        else:
            entry = WeeklyEntry(
                student_id=student_id,
                subject_id=subject_id,
                iso_week=week,
                iso_year=year,
                ranking=ranking,
                created_by=current_user.id,
                created_at=now,
            )
            db.session.add(entry)
            db.session.flush()
            log_audit(
                user       = current_user,
                action     = "create",
                model_name = "WeeklyEntry",
                record_id  = entry.id,
                field_name = "ranking",
                new_value  = ranking,
            )
        saved += 1

    db.session.commit()

    if saved:
        flash(f"Saved {saved} ranking(s) for week of {_week_label(week, year)}.", "success")
    if locked:
        flash(f"{locked} entry/entries were locked (grace period expired or term locked).", "error")
    if denied:
        flash(f"{denied} entry/entries were skipped — not in your assigned subjects.", "error")

    return redirect(url_for("entry.form"))
