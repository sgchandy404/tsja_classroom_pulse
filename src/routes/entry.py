import datetime
from io import BytesIO
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, send_file
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

    selected_grade      = request.args.get("grade", grades[0] if grades else None)
    selected_subject_id = request.args.get("subject_id", type=int)
    selected_ft_year    = request.args.get("ft_year",  type=int) or cur_year
    selected_ft_month   = request.args.get("ft_month", type=int) or cur_month
    selected_ft_period  = request.args.get("ft_period",type=int) or cur_period
    return render_template(
        "entry/form.html",
        grades=grades,
        selected_grade=selected_grade,
        selected_subject_id=selected_subject_id,
        rankings=RANKINGS,
        current_year=selected_ft_year,
        current_month=selected_ft_month,
        current_period=selected_ft_period,
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


@entry_bp.route("/existing")
@login_required
def existing():
    """Return saved rankings for a subject + fortnight so the form can pre-fill."""
    subject_id = request.args.get("subject_id", type=int)
    ft_year    = request.args.get("ft_year",    type=int)
    ft_month   = request.args.get("ft_month",   type=int)
    ft_period  = request.args.get("ft_period",  type=int)
    if not all([subject_id, ft_year, ft_month, ft_period]):
        return jsonify({})
    entries = FortnightEntry.query.filter_by(
        subject_id=subject_id,
        ft_year=ft_year, ft_month=ft_month, ft_period=ft_period,
    ).all()
    # key: "studentId_rubricId" → ranking string
    data = {f"{e.student_id}_{e.rubric_id}": e.ranking for e in entries}
    return jsonify(data)


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


@entry_bp.route("/export")
@login_required
def export_template():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, Protection
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    subject_id = request.args.get("subject_id", type=int)
    ft_year    = request.args.get("ft_year",    type=int)
    ft_month   = request.args.get("ft_month",   type=int)
    ft_period  = request.args.get("ft_period",  type=int)
    grade      = request.args.get("grade", "")

    if not all([subject_id, ft_year, ft_month, ft_period, grade]):
        flash("Select grade, subject and fortnight before exporting.", "error")
        return redirect(url_for("entry.form"))

    p = get_perms()
    subject  = db.session.get(Subject, subject_id)
    if not subject:
        flash("Subject not found.", "error")
        return redirect(url_for("entry.form"))

    rubrics  = [r for r in subject.rubrics if r.is_active]
    students = (Student.query.filter_by(grade=grade, is_active=True)
                .order_by(Student.roll_number).all())
    existing = {
        (e.student_id, e.rubric_id): e.ranking
        for e in FortnightEntry.query.filter_by(
            subject_id=subject_id,
            ft_year=ft_year, ft_month=ft_month, ft_period=ft_period,
        ).all()
    }

    wb = Workbook()

    # ── Entry sheet ────────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Entry"

    HDR_FILL  = PatternFill("solid", fgColor="1E293B")   # slate-800
    HDR_FONT  = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    ID_FILL   = PatternFill("solid", fgColor="F1F5F9")   # slate-100
    CELL_FONT = Font(name="Arial", size=10)
    CENTER    = Alignment(horizontal="center", vertical="center")
    WRAP      = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    thin      = Side(style="thin", color="E2E8F0")
    BORDER    = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Row 1: column headers
    ws.cell(1, 1, "Roll No").font  = HDR_FONT
    ws.cell(1, 1).fill             = HDR_FILL
    ws.cell(1, 1).alignment        = CENTER
    ws.cell(1, 1).border           = BORDER
    ws.cell(1, 2, "Student Name").font = HDR_FONT
    ws.cell(1, 2).fill             = HDR_FILL
    ws.cell(1, 2).alignment        = CENTER
    ws.cell(1, 2).border           = BORDER

    for i, rubric in enumerate(rubrics):
        col   = i + 3
        label = rubric.name + (" (Optional)" if not rubric.is_required else "")
        c = ws.cell(1, col, label)
        c.font      = HDR_FONT
        c.fill      = HDR_FILL
        c.alignment = WRAP
        c.border    = BORDER

    # Dropdown validation for ranking columns
    dv = DataValidation(
        type="list",
        formula1='"Working Towards,Meets Expectations,Exceeds Expectations"',
        allow_blank=True,
        showDropDown=False,
        showErrorMessage=True,
        errorTitle="Invalid ranking",
        error="Choose: Working Towards, Meets Expectations, or Exceeds Expectations",
    )
    ws.add_data_validation(dv)

    # Data rows
    ALT_FILL = PatternFill("solid", fgColor="F8FAFC")
    for r_idx, student in enumerate(students, start=2):
        fill = ALT_FILL if r_idx % 2 == 0 else None

        c = ws.cell(r_idx, 1, student.roll_number)
        c.font = CELL_FONT; c.alignment = CENTER; c.border = BORDER
        if fill: c.fill = fill

        c = ws.cell(r_idx, 2, student.name)
        c.font = CELL_FONT; c.alignment = WRAP; c.border = BORDER
        if fill: c.fill = fill

        for i, rubric in enumerate(rubrics):
            col   = i + 3
            value = existing.get((student.id, rubric.id), "")
            c     = ws.cell(r_idx, col, value)
            c.font = CELL_FONT; c.alignment = CENTER; c.border = BORDER
            if fill: c.fill = fill
            dv.add(c)

    # Column widths
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 24
    for i in range(len(rubrics)):
        ws.column_dimensions[get_column_letter(i + 3)].width = 22
    ws.row_dimensions[1].height = 36

    # Freeze header row + student name columns
    ws.freeze_panes = "C2"

    # ── META sheet (hidden — used by import to map IDs) ────────────────────
    wm = wb.create_sheet("META")
    wm.sheet_state = "hidden"
    wm["A1"] = "subject_id";  wm["B1"] = subject_id
    wm["A2"] = "ft_year";     wm["B2"] = ft_year
    wm["A3"] = "ft_month";    wm["B3"] = ft_month
    wm["A4"] = "ft_period";   wm["B4"] = ft_period
    wm["A5"] = "grade";       wm["B5"] = grade
    # Row 7: rubric IDs (col B onwards)
    wm["A7"] = "rubric_ids"
    for i, rubric in enumerate(rubrics):
        wm.cell(7, i + 2, rubric.id)
    # Rows 8+: student_id per student row (same order as Entry sheet row 2+)
    wm["A8"] = "student_ids"
    for i, student in enumerate(students):
        wm.cell(8, i + 2, student.id)

    # Stream to client
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    is_template = request.args.get("template") == "1"
    prefix   = "TEMPLATE_" if is_template else ""
    filename = f"{prefix}{grade.replace(' ', '_')}_{subject.name.replace(' ', '_')}_Fortnightly_Evaluation.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@entry_bp.route("/import", methods=["POST"])
@login_required
def import_rankings():
    from openpyxl import load_workbook

    file = request.files.get("xlsx_file")
    if not file or not file.filename.endswith(".xlsx"):
        flash("Please upload a valid .xlsx file.", "error")
        return redirect(url_for("entry.form"))

    p   = get_perms()
    now = datetime.datetime.utcnow()

    try:
        wb = load_workbook(BytesIO(file.read()), data_only=True)
    except Exception:
        flash("Could not read the file. Make sure it's an unmodified export template.", "error")
        return redirect(url_for("entry.form"))

    if "META" not in wb.sheetnames or "Entry" not in wb.sheetnames:
        flash("File is missing required sheets. Use the exported template.", "error")
        return redirect(url_for("entry.form"))

    wm = wb["META"]
    ws = wb["Entry"]

    try:
        subject_id = int(wm["B1"].value)
        ft_year    = int(wm["B2"].value)
        ft_month   = int(wm["B3"].value)
        ft_period  = int(wm["B4"].value)
        grade      = str(wm["B5"].value)
        rubric_ids = [int(wm.cell(7, c).value) for c in range(2, ws.max_column + 2) if wm.cell(7, c).value]
        student_ids = [int(wm.cell(8, c).value) for c in range(2, len(rubric_ids) + 100) if wm.cell(8, c).value]
    except (TypeError, ValueError):
        flash("META sheet is corrupt. Re-export and try again.", "error")
        return redirect(url_for("entry.form"))

    subject = db.session.get(Subject, subject_id)
    if not subject or not p.can_enter(subject.grade, subject_id):
        flash("You don't have permission to import rankings for this subject.", "error")
        return redirect(url_for("entry.form"))

    saved = skipped = invalid = 0
    required_rubric_ids = {r.id for r in subject.rubrics if r.is_active and r.is_required}
    # track which students are missing at least one required rubric after import
    students_incomplete = set()

    for row_offset, student_id in enumerate(student_ids):
        xl_row = row_offset + 2  # data starts at row 2
        filled_required = set()
        for col_offset, rubric_id in enumerate(rubric_ids):
            xl_col  = col_offset + 3  # data starts at col 3
            raw_val = ws.cell(xl_row, xl_col).value
            if raw_val is None or str(raw_val).strip() == "":
                skipped += 1
                continue
            ranking = str(raw_val).strip()
            if ranking not in RANKINGS:
                invalid += 1
                continue

            rubric = db.session.get(Rubric, rubric_id)
            if not rubric:
                continue

            existing = FortnightEntry.query.filter_by(
                student_id=student_id, rubric_id=rubric_id,
                ft_year=ft_year, ft_month=ft_month, ft_period=ft_period,
            ).first()

            if existing:
                if not p.can_edit_entry(existing):
                    skipped += 1
                    continue
                old = existing.ranking
                existing.ranking    = ranking
                existing.updated_by = current_user.id
                existing.updated_at = now
                log_audit(user=current_user, action="edit", model_name="FortnightEntry", record_id=existing.id,
                          field_name="ranking", old_value=old, new_value=ranking,
                          note="Admin override" if p.is_admin and not p.within_grace_period(existing) else None)
            else:
                entry = FortnightEntry(
                    student_id=student_id, subject_id=subject_id, rubric_id=rubric_id,
                    ft_year=ft_year, ft_month=ft_month, ft_period=ft_period,
                    ranking=ranking, created_by=current_user.id, created_at=now,
                )
                db.session.add(entry)
                db.session.flush()
                log_audit(user=current_user, action="create", model_name="FortnightEntry", record_id=entry.id,
                          field_name="ranking", old_value=None, new_value=ranking)
            saved += 1
            if rubric_id in required_rubric_ids:
                filled_required.add(rubric_id)

        if filled_required < required_rubric_ids:
            students_incomplete.add(student_id)

    db.session.commit()

    period_label = fortnight_label(ft_year, ft_month, ft_period)
    complete_count = len(student_ids) - len(students_incomplete)
    if saved:
        msg = f"Imported rankings for {complete_count} of {len(student_ids)} students · {subject.name} · {period_label}."
        if students_incomplete:
            msg += f" {len(students_incomplete)} student(s) still need required rubrics - finish them manually."
        flash(msg, "success" if not students_incomplete else "warning")
    if invalid:
        flash(f"{invalid} cell(s) had unrecognised values and were skipped.", "error")

    return redirect(url_for("entry.form", grade=grade,
                             subject_id=subject_id,
                             ft_year=ft_year, ft_month=ft_month, ft_period=ft_period))


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

    saved = 0
    locked = 0
    denied = 0
    skipped = 0
    incomplete_students = set()
    students_saved = set()
    subject_ref = None
    now = datetime.datetime.utcnow()

    for (student_id, rubric_id), ranking in raw_entries.items():
        if ranking is None:
            rubric = db.session.get(Rubric, rubric_id)
            if rubric and rubric.is_required:
                incomplete_students.add(student_id)
            skipped += 1
            continue

        rubric = db.session.get(Rubric, rubric_id)
        if not rubric:
            continue
        subject = db.session.get(Subject, rubric.subject_id)
        if not subject:
            continue
        if subject_ref is None:
            subject_ref = subject

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
        students_saved.add(student_id)

    db.session.commit()

    period_label = fortnight_label(ft_year, ft_month, ft_period)
    all_student_ids = {sid for (sid, _) in raw_entries}
    complete_count  = len(all_student_ids) - len(incomplete_students)
    if saved:
        student_label = f"{len(students_saved)} student" + ("s" if len(students_saved) != 1 else "")
        flash(
            f"Saved data for {student_label}. "
            f"{complete_count} of {len(all_student_ids)} students fully complete for {period_label}.",
            "success" if not incomplete_students else "warning"
        )
    if locked:
        flash(f"{locked} entry/entries were locked (grace period expired or term locked).", "error")
    if denied:
        flash(f"{denied} entry/entries were skipped - not in your assigned subjects.", "error")

    return redirect(url_for("entry.form"))
