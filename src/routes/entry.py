import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from models import (
    db, Student, Subject, Rubric, FortnightEntry, RANKINGS,
    date_to_fortnight, fortnight_label, prev_fortnight,
)
from permissions import perms as get_perms, log_audit

entry_bp = Blueprint("entry", __name__, url_prefix="/entry")


def _fortnight_options(centre_year, centre_month, centre_period, past=6, future=1):
    """Return list of {year, month, period, label} for a window around the given fortnight."""
    options = []
    y, m, p = centre_year, centre_month, centre_period
    # step back 'past' fortnights
    steps_back = []
    cy, cm, cp = y, m, p
    for _ in range(past):
        cy, cm, cp = prev_fortnight(cy, cm, cp)
        steps_back.append((cy, cm, cp))
    steps_back.reverse()
    for cy, cm, cp in steps_back:
        options.append({"year": cy, "month": cm, "period": cp,
                        "label": fortnight_label(cy, cm, cp)})
    options.append({"year": y, "month": m, "period": p,
                    "label": fortnight_label(y, m, p)})
    # one future fortnight
    from models import next_fortnight
    ny, nm, np_ = next_fortnight(y, m, p)
    for _ in range(future):
        options.append({"year": ny, "month": nm, "period": np_,
                        "label": fortnight_label(ny, nm, np_)})
        ny, nm, np_ = next_fortnight(ny, nm, np_)
    return options


@entry_bp.route("/")
@login_required
def form():
    p = get_perms()
    all_grades = [
        r[0] for r in
        db.session.query(Student.grade)
        .filter(Student.is_active == True)
        .distinct().order_by(Student.grade).all()
    ]

    enterable = p.enterable_grades()
    grades = all_grades if enterable is None else [g for g in all_grades if g in enterable]

    today = datetime.date.today()
    cur_year, cur_month, cur_period = date_to_fortnight(today)

    selected_grade = request.args.get("grade", grades[0] if grades else None)
    return render_template(
        "entry/form.html",
        grades=grades,
        selected_grade=selected_grade,
        rankings=RANKINGS,
        current_year=cur_year,
        current_month=cur_month,
        current_period=cur_period,
        fortnight_options=_fortnight_options(cur_year, cur_month, cur_period),
    )


@entry_bp.route("/students")
@login_required
def students():
    grade = request.args.get("grade", "")
    p = get_perms()
    if not p.can_view_grade(grade):
        return jsonify([])
    rows = (Student.query.filter_by(grade=grade, is_active=True)
            .order_by(Student.roll_number).all())
    return jsonify([{"id": s.id, "name": s.name, "roll_number": s.roll_number}
                    for s in rows])


@entry_bp.route("/subjects")
@login_required
def subjects():
    grade = request.args.get("grade", "")
    p = get_perms()
    q = Subject.query.filter_by(grade=grade, is_active=True).order_by(Subject.name)
    rows = q.all()

    ep = p.enterable_pairs()
    if ep is not None:
        rows = [s for s in rows if (grade, s.id) in ep]

    # Include rubrics for each subject
    result = []
    for s in rows:
        rubrics = [
            {"id": r.id, "name": r.name, "is_required": r.is_required}
            for r in s.rubrics if r.is_active
        ]
        result.append({"id": s.id, "name": s.name, "rubrics": rubrics})
    return jsonify(result)


@entry_bp.route("/", methods=["POST"])
@login_required
def submit():
    ft_year   = int(request.form.get("ft_year"))
    ft_month  = int(request.form.get("ft_month"))
    ft_period = int(request.form.get("ft_period"))
    p = get_perms()

    # Parse form: ranking_<student_id>_<rubric_id> = ranking value (or "")
    raw_entries = {}
    for key, value in request.form.items():
        if key.startswith("ranking_"):
            parts = key.split("_", 2)
            if len(parts) == 3:
                _, student_id, rubric_id = parts
                if value in RANKINGS or value == "":
                    raw_entries[(int(student_id), int(rubric_id))] = value or None

    if not raw_entries:
        flash("No rankings submitted.", "error")
        return redirect(url_for("entry.form"))

    # Validate required rubrics aren't left blank (except for optional)
    missing_required = []
    for (student_id, rubric_id), ranking in raw_entries.items():
        if ranking is None:
            rubric = db.session.get(Rubric, rubric_id)
            if rubric and rubric.is_required:
                missing_required.append(rubric.name)

    if missing_required:
        names = ", ".join(sorted(set(missing_required)))
        flash(f"Required rubrics left blank: {names}. Please complete them or leave optional rubrics blank.", "error")
        return redirect(url_for("entry.form"))

    saved = 0
    locked = 0
    denied = 0
    skipped = 0
    now = datetime.datetime.utcnow()

    for (student_id, rubric_id), ranking in raw_entries.items():
        # Skip blanks for optional rubrics — they're intentionally unassessed
        if ranking is None:
            skipped += 1
            continue

        rubric = db.session.get(Rubric, rubric_id)
        if not rubric:
            continue
        subject = db.session.get(Subject, rubric.subject_id)
        if not subject:
            continue

        if not p.can_enter(subject.grade, subject.id):
            denied += 1
            continue

        existing = FortnightEntry.query.filter_by(
            student_id=student_id,
            rubric_id=rubric_id,
            ft_year=ft_year,
            ft_month=ft_month,
            ft_period=ft_period,
        ).first()

        if existing:
            if not p.can_edit_entry(existing):
                locked += 1
                continue
            old_ranking = existing.ranking
            existing.ranking    = ranking
            existing.updated_by = current_user.id
            existing.updated_at = now
            log_audit(
                user       = current_user,
                action     = "edit",
                model_name = "FortnightEntry",
                record_id  = existing.id,
                field_name = "ranking",
                old_value  = old_ranking,
                new_value  = ranking,
                note       = "Admin override" if p.is_admin and not p.within_grace_period(existing) else None,
            )
        else:
            entry = FortnightEntry(
                student_id=student_id,
                subject_id=subject.id,
                rubric_id=rubric_id,
                ft_year=ft_year,
                ft_month=ft_month,
                ft_period=ft_period,
                ranking=ranking,
                created_by=current_user.id,
                created_at=now,
            )
            db.session.add(entry)
            db.session.flush()
            log_audit(
                user       = current_user,
                action     = "create",
                model_name = "FortnightEntry",
                record_id  = entry.id,
                field_name = "ranking",
                new_value  = ranking,
            )
        saved += 1

    db.session.commit()

    period_label = fortnight_label(ft_year, ft_month, ft_period)
    if saved:
        flash(f"Saved {saved} ranking(s) for {period_label}.", "success")
    if locked:
        flash(f"{locked} entry/entries were locked (grace period expired or term locked).", "error")
    if denied:
        flash(f"{denied} entry/entries were skipped — not in your assigned subjects.", "error")

    return redirect(url_for("entry.form"))
