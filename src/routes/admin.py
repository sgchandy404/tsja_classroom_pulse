"""
Admin blueprint — user management, grade/subject management, app config.
All routes require the 'admin' role.
"""
import io
import re
import secrets
import openpyxl
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import login_required, current_user
from models import db, User, UserRole, Subject, Student, Grade, Rubric, AppConfig, FortnightEntry, ROLES
from permissions import require_role, log_audit, perms as get_perms

# Display label → internal role key
_ROLE_LABEL_MAP = {
    "teacher":     "teacher",
    "in-charge":   "incharge",
    "incharge":    "incharge",
    "coordinator": "coordinator",
    "admin":       "admin",
}

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_grades():
    """Return active grade names, preferring Grade registry; fallback to union-of-strings."""
    active = Grade.query.filter_by(is_active=True).order_by(Grade.name).all()
    if active:
        return [g.name for g in active]
    # Fallback: derive from existing Student/Subject rows
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]
    subj_grades = [r[0] for r in db.session.query(Subject.grade).distinct().order_by(Subject.grade).all()]
    return sorted(set(grades) | set(subj_grades))


def _roles_summary(user: User) -> str:
    """Return a plain-English summary of a user's current role assignments."""
    parts = []
    for r in user.roles:
        if r.role == "admin":
            parts.append("Admin")
        elif r.role == "coordinator":
            parts.append("Coordinator")
        elif r.role == "incharge":
            parts.append(f"In-Charge ({r.grade})")
        elif r.role == "teacher" and r.subject_id:
            subj = db.session.get(Subject, r.subject_id)
            subj_name = subj.name if subj else f"subject #{r.subject_id}"
            parts.append(f"Teacher ({r.grade} · {subj_name})")
    return ", ".join(sorted(parts)) if parts else "No roles"


def _save_roles(user: User, form):
    """
    Parse role assignments from the submitted form and replace all UserRole rows.
    Form fields:
      role_admin=1, role_coordinator=1        — global roles
      incharge_<grade>=1                       — In-Charge for a grade
      teacher_<grade>_<subject_id>=1          — Teacher for a subject
    """
    # Clear existing
    UserRole.query.filter_by(user_id=user.id).delete()
    db.session.flush()

    if form.get("role_admin"):
        db.session.add(UserRole(user_id=user.id, role="admin"))

    if form.get("role_coordinator"):
        db.session.add(UserRole(user_id=user.id, role="coordinator"))

    for key in form:
        if key.startswith("incharge_"):
            grade = key[len("incharge_"):]
            db.session.add(UserRole(user_id=user.id, role="incharge", grade=grade))

        elif key.startswith("teacher_"):
            parts = key.split("_", 2)   # teacher_<grade>_<subject_id>
            if len(parts) == 3:
                _, grade, subject_id_str = parts
                try:
                    subject_id = int(subject_id_str)
                    db.session.add(UserRole(user_id=user.id, role="teacher",
                                           grade=grade, subject_id=subject_id))
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# Routes: user management
# ---------------------------------------------------------------------------

@admin_bp.route("/")
@login_required
@require_role("admin")
def index():
    users = User.query.order_by(User.username).all()
    return render_template("admin/index.html", users=users)


@admin_bp.route("/users/import", methods=["GET", "POST"])
@login_required
@require_role("admin")
def import_users():
    if request.method == "GET":
        return render_template("admin/import_users.html")

    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return render_template("admin/import_users.html")

    try:
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    except Exception:
        flash("Could not read the file. Please upload a valid .xlsx file.", "error")
        return render_template("admin/import_users.html")

    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    # Pre-load lookup tables
    existing_usernames = {u.username.lower() for u in User.query.all()}
    valid_grades = {g.name for g in Grade.query.filter_by(is_active=True).all()}
    # subject lookup: (grade, name_lower) → subject_id
    all_subjects = Subject.query.filter_by(is_active=True).all()
    subject_map = {(s.grade, s.name.lower()): s.id for s in all_subjects}
    # existing in-charge assignments: grade → username (for conflict detection)
    existing_incharge = {}
    for ur in UserRole.query.filter_by(role="incharge").all():
        if ur.grade and ur.user:
            existing_incharge.setdefault(ur.grade, []).append(ur.user.username)

    succeeded = []  # {"name", "username", "temp_password", "roles_summary"}
    failed = []     # {"row", "username", "reasons": [...]}
    seen_usernames_in_file = {}  # username_lower → row number

    def _parse_csv(cell):
        if not cell:
            return []
        return [v.strip() for v in str(cell).split(",") if v.strip()]

    for idx, row in enumerate(rows, start=2):
        name      = str(row[0]).strip() if row[0] is not None else ""
        username  = str(row[1]).strip() if row[1] is not None else ""
        roles_raw = _parse_csv(row[2] if len(row) > 2 else None)
        grades_raw= _parse_csv(row[3] if len(row) > 3 else None)
        subjs_raw = _parse_csv(row[4] if len(row) > 4 else None)

        if not name and not username:
            continue  # blank row

        row_errors = []

        if not name:
            row_errors.append("Name is required")
        if not username:
            row_errors.append("Email/Username is required")
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", username):
            row_errors.append(f"'{username}' is not a valid email address")
        elif username.lower() in existing_usernames:
            row_errors.append(f"'{username}' already exists")
        elif username.lower() in seen_usernames_in_file:
            row_errors.append(
                f"'{username}' duplicated in this file (first seen row {seen_usernames_in_file[username.lower()]})"
            )

        # Validate roles
        parsed_roles = []
        for r in roles_raw:
            key = _ROLE_LABEL_MAP.get(r.lower())
            if key:
                parsed_roles.append(key)
            else:
                row_errors.append(f"Unknown role '{r}' (valid: Teacher, In-Charge, Coordinator, Admin)")

        # Validate grades
        bad_grades = [g for g in grades_raw if g not in valid_grades]
        if bad_grades:
            row_errors.append(f"Grade(s) not found: {', '.join(bad_grades)}")
        valid_assigned_grades = [g for g in grades_raw if g in valid_grades]

        # In-Charge conflict check
        if "incharge" in parsed_roles:
            for g in valid_assigned_grades:
                existing = existing_incharge.get(g, [])
                if existing:
                    row_errors.append(
                        f"Grade '{g}' already has an In-Charge ({', '.join(existing)}) — resolve conflict first"
                    )

        # Validate subjects against assigned grades
        teacher_assignments = []  # list of (grade, subject_id)
        if "teacher" in parsed_roles or "incharge" in parsed_roles:
            for sname in subjs_raw:
                matches = [
                    (g, subject_map[(g, sname.lower())])
                    for g in valid_assigned_grades
                    if (g, sname.lower()) in subject_map
                ]
                if not matches:
                    row_errors.append(
                        f"Subject '{sname}' not found in assigned grade(s)"
                    )
                else:
                    teacher_assignments.extend(matches)

        if row_errors:
            failed.append({
                "row": idx,
                "username": username or "(blank)",
                "name": name or "(blank)",
                "reasons": row_errors,
            })
            continue

        # All valid — create user
        temp_password = secrets.token_urlsafe(10)
        user = User(
            username=username,
            name=name,
            active=True,
            must_change_password=True,
        )
        user.set_password(temp_password)
        db.session.add(user)
        db.session.flush()

        for role_key in set(parsed_roles):
            if role_key in ("admin", "coordinator"):
                db.session.add(UserRole(user_id=user.id, role=role_key))
            elif role_key == "incharge":
                for g in valid_assigned_grades:
                    db.session.add(UserRole(user_id=user.id, role="incharge", grade=g))
            elif role_key == "teacher":
                for g, sid in set(teacher_assignments):
                    db.session.add(UserRole(user_id=user.id, role="teacher", grade=g, subject_id=sid))

        seen_usernames_in_file[username.lower()] = idx
        existing_usernames.add(username.lower())
        roles_summary = ", ".join(sorted(set(parsed_roles)))
        succeeded.append({
            "name": name,
            "username": username,
            "temp_password": temp_password,
            "roles_summary": roles_summary,
        })

    if succeeded:
        log_audit(
            user=current_user, action="create", model_name="User", record_id=0,
            field_name="bulk_import",
            new_value=f"{len(succeeded)} users imported; {len(failed)} failed",
            note=f"File: {file.filename}",
        )
        db.session.commit()

    if succeeded and not failed:
        flash(f"Imported {len(succeeded)} user(s) successfully.", "success")
    elif succeeded and failed:
        flash(f"Imported {len(succeeded)} user(s). {len(failed)} row(s) had errors.", "warning")
    else:
        flash(f"No users imported. {len(failed)} row(s) had errors.", "error")

    return render_template("admin/import_users.html",
                           succeeded=succeeded, failed_rows=failed)


@admin_bp.route("/users/export")
@login_required
@require_role("admin")
def export_users():
    users = User.query.order_by(User.username).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Users"

    bold = openpyxl.styles.Font(bold=True)
    headers = ["Name", "Email", "Role(s)", "Assigned Grade(s)", "Assigned Subject(s)", "Status"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = bold

    for ri, u in enumerate(users, 2):
        roles_set = set()
        grades_set = set()
        subject_ids = set()

        for r in u.roles:
            if r.role == "admin":       roles_set.add("Admin")
            elif r.role == "coordinator": roles_set.add("Coordinator")
            elif r.role == "incharge":
                roles_set.add("In-Charge")
                if r.grade: grades_set.add(r.grade)
            elif r.role == "teacher":
                roles_set.add("Teacher")
                if r.grade: grades_set.add(r.grade)
                if r.subject_id: subject_ids.add(r.subject_id)

        subj_names = []
        if subject_ids:
            subjs = Subject.query.filter(Subject.id.in_(subject_ids)).order_by(Subject.name).all()
            subj_names = [s.name for s in subjs]

        ws.cell(row=ri, column=1, value=u.name or "")
        ws.cell(row=ri, column=2, value=u.username)
        ws.cell(row=ri, column=3, value=", ".join(sorted(roles_set)))
        ws.cell(row=ri, column=4, value=", ".join(sorted(grades_set)))
        ws.cell(row=ri, column=5, value=", ".join(subj_names))
        ws.cell(row=ri, column=6, value="Active" if u.active else "Inactive")

    for col, width in zip("ABCDEF", [30, 35, 30, 25, 40, 12]):
        ws.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    log_audit(user=current_user, action="create", model_name="User", record_id=0,
              field_name="export", new_value=f"Exported {len(users)} users")
    db.session.commit()

    return send_file(buf, as_attachment=True, download_name="Users_Export.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_bp.route("/users/template")
@login_required
@require_role("admin")
def users_import_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Users"

    bold = openpyxl.styles.Font(bold=True)
    headers = ["Name", "Email", "Role(s)", "Assigned Grade(s)", "Assigned Subject(s)"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = bold

    # Hint row
    ws.cell(row=2, column=1, value="e.g. Priya Sharma")
    ws.cell(row=2, column=2, value="priya@school.edu")
    ws.cell(row=2, column=3, value="Teacher, In-Charge")
    ws.cell(row=2, column=4, value="Grade 7, Grade 8")
    ws.cell(row=2, column=5, value="Maths, Science")

    hint_font = openpyxl.styles.Font(italic=True, color="999999")
    for col in range(1, 6):
        ws.cell(row=2, column=col).font = hint_font

    for col, width in zip("ABCDE", [30, 35, 30, 25, 40]):
        ws.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return send_file(buf, as_attachment=True, download_name="TEMPLATE_Users_Import.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@require_role("admin")
def new_user():
    grades = _all_grades()
    subjects_by_grade = {
        g: Subject.query.filter_by(grade=g, is_active=True).order_by(Subject.name).all()
        for g in grades
    }

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Username and password are required.", "error")
        elif User.query.filter_by(username=username).first():
            flash(f"Username '{username}' is already taken.", "error")
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            _save_roles(user, request.form)
            log_audit(user=current_user, action="create", model_name="User",
                      record_id=user.id, field_name="username", new_value=username)
            db.session.commit()
            flash(f"User '{username}' created.", "success")
            return redirect(url_for("admin.index"))

    return render_template("admin/new_user.html",
                           grades=grades, subjects_by_grade=subjects_by_grade)


@admin_bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@login_required
@require_role("admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    grades = _all_grades()
    subjects_by_grade = {
        g: Subject.query.filter_by(grade=g, is_active=True).order_by(Subject.name).all()
        for g in grades
    }

    if request.method == "POST":
        action = request.form.get("action")

        if action == "toggle_active":
            if user.username == "admin" and user.active:
                flash("Cannot deactivate the primary admin account.", "error")
            else:
                old_state = user.active
                user.active = not user.active
                state = "activated" if user.active else "deactivated"
                log_audit(user=current_user, action="edit", model_name="User",
                          record_id=user.id, field_name="active",
                          old_value=str(old_state), new_value=str(user.active),
                          note=f"User {state}")
                db.session.commit()
                flash(f"User '{user.username}' {state}.", "success")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        if action == "change_password":
            pw = request.form.get("new_password", "").strip()
            if len(pw) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                user.set_password(pw)
                log_audit(user=current_user, action="edit", model_name="User",
                          record_id=user.id, field_name="password", new_value="[changed]")
                db.session.commit()
                flash("Password updated.", "success")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        if action == "save_roles":
            old_summary = _roles_summary(user)
            _save_roles(user, request.form)
            db.session.flush()
            new_summary = _roles_summary(user)
            log_audit(user=current_user, action="edit", model_name="User",
                      record_id=user.id, field_name="roles",
                      old_value=old_summary, new_value=new_summary)
            db.session.commit()
            flash(f"Role assignments for '{user.username}' saved.", "success")
            return redirect(url_for("admin.edit_user", user_id=user_id))

    # Build current assignment sets for template checkboxes
    current_roles = user.role_names
    current_incharge_grades = {r.grade for r in user.roles if r.role == "incharge" and r.grade}
    current_teacher_pairs = {(r.grade, r.subject_id) for r in user.roles if r.role == "teacher"}

    return render_template(
        "admin/edit_user.html",
        user=user,
        grades=grades,
        subjects_by_grade=subjects_by_grade,
        current_roles=current_roles,
        current_incharge_grades=current_incharge_grades,
        current_teacher_pairs=current_teacher_pairs,
    )


# ---------------------------------------------------------------------------
# Routes: app config
# ---------------------------------------------------------------------------

@admin_bp.route("/config", methods=["GET", "POST"])
@login_required
@require_role("admin")
def config():
    if request.method == "POST":
        try:
            hours = int(request.form.get("grace_period_hours", 48))
            if hours < 0:
                raise ValueError
            old = AppConfig.get_value("grace_period_hours", "48")
            AppConfig.set_value("grace_period_hours", hours)
            log_audit(user=current_user, action="edit", model_name="AppConfig",
                      record_id=0, field_name="grace_period_hours",
                      old_value=str(old), new_value=str(hours))
            db.session.commit()
            flash(f"Grace period set to {hours} hours.", "success")
        except ValueError:
            flash("Grace period must be a non-negative integer.", "error")
        return redirect(url_for("admin.config"))

    grace = AppConfig.get_value("grace_period_hours", "48")
    return render_template("admin/config.html", grace_period_hours=grace)


# ---------------------------------------------------------------------------
# Routes: grade management (admin only)
# ---------------------------------------------------------------------------

@admin_bp.route("/grades")
@login_required
@require_role("admin")
def grades():
    all_grades = Grade.query.order_by(Grade.name).all()
    # Pre-compute counts for display
    grade_subject_counts = {
        g.name: Subject.query.filter_by(grade=g.name, is_active=True).count()
        for g in all_grades
    }
    grade_student_counts = {
        g.name: Student.query.filter_by(grade=g.name, is_active=True).count()
        for g in all_grades
    }
    return render_template("admin/grades.html", grades=all_grades,
                           grade_subject_counts=grade_subject_counts,
                           grade_student_counts=grade_student_counts)


@admin_bp.route("/grades/new", methods=["POST"])
@login_required
@require_role("admin")
def new_grade():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Grade name is required.", "error")
        return redirect(url_for("admin.grades"))
    if Grade.query.filter_by(name=name).first():
        flash(f"Grade '{name}' already exists.", "error")
        return redirect(url_for("admin.grades"))
    grade = Grade(name=name, is_active=True)
    db.session.add(grade)
    db.session.flush()
    log_audit(user=current_user, action="create", model_name="Grade",
              record_id=grade.id, field_name="name", new_value=name)
    db.session.commit()
    flash(f"Grade '{name}' added.", "success")
    return redirect(url_for("admin.grades"))


@admin_bp.route("/grades/<int:grade_id>/deactivate", methods=["GET", "POST"])
@login_required
@require_role("admin")
def deactivate_grade(grade_id):
    grade = db.session.get(Grade, grade_id)
    if not grade:
        abort(404)
    if request.method == "POST":
        grade.is_active = False
        log_audit(user=current_user, action="edit", model_name="Grade",
                  record_id=grade.id, field_name="is_active",
                  old_value="True", new_value="False",
                  note=f"Deactivated grade '{grade.name}'")
        db.session.commit()
        flash(f"Grade '{grade.name}' deactivated.", "success")
        return redirect(url_for("admin.grades"))
    # Gather warning counts
    student_count = Student.query.filter_by(grade=grade.name, is_active=True).count()
    subject_count = Subject.query.filter_by(grade=grade.name, is_active=True).count()
    entry_count = (
        FortnightEntry.query.join(Student)
        .filter(Student.grade == grade.name)
        .count()
    )
    return render_template("admin/deactivate_grade_confirm.html",
                           grade=grade,
                           student_count=student_count,
                           subject_count=subject_count,
                           entry_count=entry_count)


@admin_bp.route("/grades/<int:grade_id>/reactivate", methods=["POST"])
@login_required
@require_role("admin")
def reactivate_grade(grade_id):
    grade = db.session.get(Grade, grade_id)
    if not grade:
        abort(404)
    grade.is_active = True
    log_audit(user=current_user, action="edit", model_name="Grade",
              record_id=grade.id, field_name="is_active",
              old_value="False", new_value="True",
              note=f"Reactivated grade '{grade.name}'")
    db.session.commit()
    flash(f"Grade '{grade.name}' reactivated.", "success")
    return redirect(url_for("admin.grades"))


# ---------------------------------------------------------------------------
# Routes: subject management (admin only)
# ---------------------------------------------------------------------------

@admin_bp.route("/subjects")
@login_required
@require_role("admin")
def subjects():
    grade_filter = request.args.get("grade", "")
    q = Subject.query
    if grade_filter:
        q = q.filter_by(grade=grade_filter)
    all_subjects = q.order_by(Subject.grade, Subject.name).all()
    all_grades = _all_grades()
    # Also include inactive grades that still have subjects
    inactive_grades = [
        r[0] for r in db.session.query(Subject.grade).distinct().order_by(Subject.grade).all()
        if r[0] not in all_grades
    ]
    return render_template("admin/subjects.html",
                           subjects=all_subjects,
                           all_grades=all_grades + inactive_grades,
                           grade_filter=grade_filter)


@admin_bp.route("/subjects/new", methods=["GET", "POST"])
@login_required
@require_role("admin")
def new_subject():
    all_grades = _all_grades()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        grade = request.form.get("grade", "").strip()
        if not name or not grade:
            flash("Name and grade are required.", "error")
        elif Subject.query.filter_by(name=name, grade=grade).first():
            flash(f"Subject '{name}' already exists in {grade}.", "error")
        else:
            subj = Subject(name=name, grade=grade, is_active=True)
            db.session.add(subj)
            db.session.flush()
            log_audit(user=current_user, action="create", model_name="Subject",
                      record_id=subj.id, field_name="name",
                      new_value=f"{name} ({grade})")
            db.session.commit()
            flash(f"Subject '{name}' added to {grade}.", "success")
            return redirect(url_for("admin.subjects", grade=grade))
    return render_template("admin/new_subject.html", all_grades=all_grades,
                           preselect_grade=request.args.get("grade", ""))


@admin_bp.route("/subjects/<int:subject_id>/deactivate", methods=["GET", "POST"])
@login_required
@require_role("admin")
def deactivate_subject(subject_id):
    subj = db.session.get(Subject, subject_id)
    if not subj:
        abort(404)
    if request.method == "POST":
        subj.is_active = False
        log_audit(user=current_user, action="edit", model_name="Subject",
                  record_id=subj.id, field_name="is_active",
                  old_value="True", new_value="False",
                  note=f"Deactivated subject '{subj.name}' in {subj.grade}")
        db.session.commit()
        flash(f"Subject '{subj.name}' deactivated.", "success")
        return redirect(url_for("admin.subjects", grade=subj.grade))
    entry_count = FortnightEntry.query.filter_by(subject_id=subj.id).count()
    return render_template("admin/deactivate_subject_confirm.html",
                           subject=subj, entry_count=entry_count)


@admin_bp.route("/subjects/<int:subject_id>/reactivate", methods=["POST"])
@login_required
@require_role("admin")
def reactivate_subject(subject_id):
    subj = db.session.get(Subject, subject_id)
    if not subj:
        abort(404)
    subj.is_active = True
    log_audit(user=current_user, action="edit", model_name="Subject",
              record_id=subj.id, field_name="is_active",
              old_value="False", new_value="True",
              note=f"Reactivated subject '{subj.name}' in {subj.grade}")
    db.session.commit()
    flash(f"Subject '{subj.name}' reactivated.", "success")
    return redirect(url_for("admin.subjects", grade=subj.grade))


# ---------------------------------------------------------------------------
# Routes: student management (admin + incharge)
# ---------------------------------------------------------------------------

@admin_bp.route("/students")
@login_required
@require_role("admin", "incharge")
def students():
    p = get_perms()
    mg = p.manageable_grades()   # None = admin (all); set = incharge grades

    grade_filter = request.args.get("grade", "")
    if grade_filter and not p.can_manage_students(grade_filter):
        abort(403)

    q = Student.query
    if mg is not None:
        q = q.filter(Student.grade.in_(list(mg)))
    if grade_filter:
        q = q.filter_by(grade=grade_filter)
    all_students = q.order_by(Student.grade, Student.name).all()

    manageable = _all_grades() if mg is None else sorted(mg)
    return render_template("admin/students.html",
                           students=all_students,
                           manageable_grades=manageable,
                           grade_filter=grade_filter)


@admin_bp.route("/students/new", methods=["GET", "POST"])
@login_required
@require_role("admin", "incharge")
def new_student():
    p = get_perms()
    mg = p.manageable_grades()
    manageable = _all_grades() if mg is None else sorted(mg)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll_number", "").strip()
        grade = request.form.get("grade", "").strip()

        if not name or not grade:
            flash("Name and grade are required.", "error")
        elif not p.can_manage_students(grade):
            abort(403)
        else:
            student = Student(name=name, roll_number=roll or None, grade=grade, is_active=True)
            db.session.add(student)
            db.session.flush()
            log_audit(user=current_user, action="create", model_name="Student",
                      record_id=student.id, field_name="name",
                      new_value=f"{name} ({grade})")
            db.session.commit()
            flash(f"Student '{name}' added to {grade}.", "success")
            return redirect(url_for("admin.students", grade=grade))

    return render_template("admin/new_student.html",
                           manageable_grades=manageable,
                           preselect_grade=request.args.get("grade", ""))


@admin_bp.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
@require_role("admin", "incharge")
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)
    p = get_perms()
    if not p.can_manage_students(student.grade):
        abort(403)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll_number", "").strip()
        if not name:
            flash("Name is required.", "error")
        else:
            old_name = student.name
            student.name = name
            student.roll_number = roll or None
            log_audit(user=current_user, action="edit", model_name="Student",
                      record_id=student.id, field_name="name",
                      old_value=old_name, new_value=name)
            db.session.commit()
            flash(f"Student '{name}' updated.", "success")
            return redirect(url_for("admin.students", grade=student.grade))

    return render_template("admin/edit_student.html", student=student)


@admin_bp.route("/students/<int:student_id>/deactivate", methods=["GET", "POST"])
@login_required
@require_role("admin", "incharge")
def deactivate_student(student_id):
    student = Student.query.get_or_404(student_id)
    p = get_perms()
    if not p.can_manage_students(student.grade):
        abort(403)

    if request.method == "POST":
        student.is_active = False
        log_audit(user=current_user, action="edit", model_name="Student",
                  record_id=student.id, field_name="is_active",
                  old_value="True", new_value="False",
                  note=f"Deactivated student '{student.name}' ({student.grade})")
        db.session.commit()
        flash(f"Student '{student.name}' deactivated.", "success")
        return redirect(url_for("admin.students", grade=student.grade))

    entry_count = FortnightEntry.query.filter_by(student_id=student.id).count()
    return render_template("admin/deactivate_student_confirm.html",
                           student=student, entry_count=entry_count)


@admin_bp.route("/students/<int:student_id>/reactivate", methods=["POST"])
@login_required
@require_role("admin", "incharge")
def reactivate_student(student_id):
    student = Student.query.get_or_404(student_id)
    p = get_perms()
    if not p.can_manage_students(student.grade):
        abort(403)
    student.is_active = True
    log_audit(user=current_user, action="edit", model_name="Student",
              record_id=student.id, field_name="is_active",
              old_value="False", new_value="True",
              note=f"Reactivated student '{student.name}' ({student.grade})")
    db.session.commit()
    flash(f"Student '{student.name}' reactivated.", "success")
    return redirect(url_for("admin.students", grade=student.grade))


@admin_bp.route("/students/import", methods=["GET", "POST"])
@login_required
@require_role("admin", "incharge")
def import_students():
    p = get_perms()
    mg = p.manageable_grades()
    manageable = _all_grades() if mg is None else sorted(mg)

    if request.method == "GET":
        return render_template("admin/import_students.html",
                               manageable_grades=manageable,
                               preselect_grade=request.args.get("grade", ""))

    grade = request.form.get("grade", "").strip()
    if not grade:
        flash("Please select a grade.", "error")
        return render_template("admin/import_students.html",
                               manageable_grades=manageable,
                               preselect_grade="")

    if not p.can_manage_students(grade):
        abort(403)

    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return render_template("admin/import_students.html",
                               manageable_grades=manageable,
                               preselect_grade=grade)

    try:
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    except Exception:
        flash("Could not read the file. Please upload a valid .xlsx file.", "error")
        return render_template("admin/import_students.html",
                               manageable_grades=manageable,
                               preselect_grade=grade)

    ws = wb.active
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    # Existing active roll numbers for duplicate checking against DB
    existing_rolls = {
        s.roll_number.strip().lower()
        for s in Student.query.filter_by(grade=grade, is_active=True).all()
        if s.roll_number
    }

    succeeded = []
    failed = []
    seen_rolls_in_file = {}  # roll -> first row number (1-indexed for display)

    for idx, row in enumerate(rows, start=2):
        name = str(row[0]).strip() if row[0] is not None else ""
        roll = str(row[1]).strip() if row[1] is not None else ""

        if not name and not roll:
            continue  # blank row, skip silently

        row_errors = []
        if not name:
            row_errors.append("Name is required")
        if not roll:
            row_errors.append("Roll Number is required")
        elif roll.lower() in existing_rolls:
            row_errors.append(f"Roll Number '{roll}' already exists in {grade}")
        elif roll.lower() in seen_rolls_in_file:
            row_errors.append(
                f"Roll Number '{roll}' duplicated in this file (first seen row {seen_rolls_in_file[roll.lower()]})"
            )

        if row_errors:
            failed.append({"row": idx, "name": name or "(blank)", "roll": roll or "(blank)",
                           "reasons": row_errors})
        else:
            seen_rolls_in_file[roll.lower()] = idx
            succeeded.append({"name": name, "roll": roll})

    if succeeded:
        for item in succeeded:
            student = Student(name=item["name"], roll_number=item["roll"],
                              grade=grade, is_active=True)
            db.session.add(student)

        db.session.flush()
        log_audit(
            user=current_user,
            action="create",
            model_name="Student",
            record_id=0,
            field_name="bulk_import",
            new_value=f"{len(succeeded)} students imported into {grade}; {len(failed)} failed",
            note=f"File: {file.filename}",
        )
        db.session.commit()

    if succeeded and not failed:
        flash(f"Imported {len(succeeded)} student(s) into {grade}.", "success")
    elif succeeded and failed:
        flash(
            f"Imported {len(succeeded)} student(s) into {grade}. "
            f"{len(failed)} row(s) had errors - see details below.",
            "warning",
        )
    else:
        flash(f"No students imported. {len(failed)} row(s) had errors - see details below.", "error")

    return render_template("admin/import_students.html",
                           manageable_grades=manageable,
                           preselect_grade=grade,
                           succeeded_count=len(succeeded),
                           failed_rows=failed)


@admin_bp.route("/students/export")
@login_required
@require_role("admin", "incharge", "coordinator", "teacher")
def export_students():
    p = get_perms()
    mg = p.manageable_grades()

    grade_filter = request.args.get("grade", "").strip()

    # Teachers: restrict to grades they have assignments for (same as visible_grades)
    if not p.is_admin and not any(r.role == "incharge" for r in p._roles):
        visible = p.visible_grades()
        if visible is None:
            pass  # coordinator / shouldn't reach here
        else:
            if grade_filter and grade_filter not in visible:
                abort(403)
            if not grade_filter:
                # Export all their visible grades
                mg = visible

    q = Student.query
    if mg is not None:
        q = q.filter(Student.grade.in_(list(mg)))
    if grade_filter:
        if mg is not None and grade_filter not in mg:
            abort(403)
        q = q.filter_by(grade=grade_filter)

    students = q.order_by(Student.grade, Student.name).all()

    is_template = request.args.get("template") == "1"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Students"

    header_font = openpyxl.styles.Font(bold=True)

    if is_template:
        headers = ["Name", "Roll Number"]
        for col, h in enumerate(headers, start=1):
            ws.cell(row=1, column=col, value=h).font = header_font
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 15
    else:
        headers = ["Name", "Roll Number", "Grade", "Status"]
        for col, h in enumerate(headers, start=1):
            ws.cell(row=1, column=col, value=h).font = header_font
        for row_idx, s in enumerate(students, start=2):
            ws.cell(row=row_idx, column=1, value=s.name)
            ws.cell(row=row_idx, column=2, value=s.roll_number or "")
            ws.cell(row=row_idx, column=3, value=s.grade)
            ws.cell(row=row_idx, column=4, value="Active" if s.is_active else "Inactive")
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 15
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    grade_part = grade_filter.replace(" ", "_") if grade_filter else "All_Grades"
    filename = (f"TEMPLATE_Students_{grade_part}.xlsx" if is_template
                else f"Students_{grade_part}.xlsx")

    if not is_template:
        log_audit(
            user=current_user,
            action="create",
            model_name="Student",
            record_id=0,
            field_name="export",
            new_value=f"Exported {len(students)} students ({grade_part})",
        )
        db.session.commit()

    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Routes: rubric management (admin only, sub-resource of Subject)
# ---------------------------------------------------------------------------

def _next_display_order(subject_id: int) -> int:
    """Return display_order one past the current maximum for this subject."""
    from sqlalchemy import func
    max_order = db.session.query(func.max(Rubric.display_order)).filter_by(
        subject_id=subject_id
    ).scalar()
    return (max_order or 0) + 1


@admin_bp.route("/subjects/<int:subject_id>/rubrics", methods=["GET", "POST"])
@login_required
@require_role("admin")
def rubrics(subject_id):
    subj = db.session.get(Subject, subject_id)
    if not subj:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        is_required = request.form.get("is_required") == "1"

        if not name:
            flash("Rubric name is required.", "error")
        elif Rubric.query.filter_by(subject_id=subject_id, name=name).first():
            flash(f"A rubric named '{name}' already exists for this subject.", "error")
        else:
            rubric = Rubric(
                subject_id=subject_id,
                name=name,
                description=description,
                is_required=is_required,
                is_active=True,
                display_order=_next_display_order(subject_id),
            )
            db.session.add(rubric)
            db.session.flush()
            log_audit(user=current_user, action="create", model_name="Rubric",
                      record_id=rubric.id, field_name="name",
                      new_value=f"{name} ({'required' if is_required else 'optional'})")
            db.session.commit()
            flash(f"Rubric '{name}' added.", "success")
        return redirect(url_for("admin.rubrics", subject_id=subject_id))

    all_rubrics = Rubric.query.filter_by(subject_id=subject_id).order_by(
        Rubric.display_order, Rubric.id
    ).all()
    return render_template("admin/rubrics.html", subject=subj, rubrics=all_rubrics)


@admin_bp.route("/rubrics/<int:rubric_id>/edit", methods=["POST"])
@login_required
@require_role("admin")
def edit_rubric(rubric_id):
    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        abort(404)

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    is_required = request.form.get("is_required") == "1"

    if not name:
        flash("Rubric name is required.", "error")
        return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))

    clash = Rubric.query.filter(
        Rubric.subject_id == rubric.subject_id,
        Rubric.name == name,
        Rubric.id != rubric_id,
    ).first()
    if clash:
        flash(f"A rubric named '{name}' already exists for this subject.", "error")
        return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))

    old_name = rubric.name
    rubric.name = name
    rubric.description = description
    rubric.is_required = is_required
    log_audit(user=current_user, action="edit", model_name="Rubric",
              record_id=rubric.id, field_name="name",
              old_value=old_name,
              new_value=f"{name} ({'required' if is_required else 'optional'})")
    db.session.commit()
    flash(f"Rubric '{name}' updated.", "success")
    return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))


@admin_bp.route("/rubrics/<int:rubric_id>/toggle_required", methods=["POST"])
@login_required
@require_role("admin")
def toggle_rubric_required(rubric_id):
    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        abort(404)
    rubric.is_required = not rubric.is_required
    state = "required" if rubric.is_required else "optional"
    log_audit(user=current_user, action="edit", model_name="Rubric",
              record_id=rubric.id, field_name="is_required",
              old_value=str(not rubric.is_required), new_value=str(rubric.is_required),
              note=f"Toggled '{rubric.name}' to {state}")
    db.session.commit()
    flash(f"'{rubric.name}' is now {state}.", "success")
    return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))


@admin_bp.route("/rubrics/<int:rubric_id>/move", methods=["POST"])
@login_required
@require_role("admin")
def move_rubric(rubric_id):
    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        abort(404)
    direction = request.form.get("direction")  # "up" or "down"

    siblings = Rubric.query.filter_by(subject_id=rubric.subject_id).order_by(
        Rubric.display_order, Rubric.id
    ).all()
    ids = [r.id for r in siblings]
    idx = ids.index(rubric_id)

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(siblings):
        sibling = siblings[swap_idx]
        # Swap display_order values
        rubric.display_order, sibling.display_order = sibling.display_order, rubric.display_order
        # If they were equal, nudge them apart
        if rubric.display_order == sibling.display_order:
            if direction == "up":
                rubric.display_order -= 1
            else:
                rubric.display_order += 1
        db.session.commit()

    return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))


@admin_bp.route("/rubrics/<int:rubric_id>/deactivate", methods=["GET", "POST"])
@login_required
@require_role("admin")
def deactivate_rubric(rubric_id):
    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        abort(404)

    if request.method == "POST":
        # Guard: at least one active rubric must remain
        active_count = Rubric.query.filter_by(
            subject_id=rubric.subject_id, is_active=True
        ).count()
        if active_count <= 1:
            flash("Cannot deactivate the last active rubric for this subject.", "error")
            return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))

        rubric.is_active = False
        log_audit(user=current_user, action="edit", model_name="Rubric",
                  record_id=rubric.id, field_name="is_active",
                  old_value="True", new_value="False",
                  note=f"Deactivated rubric '{rubric.name}' on {rubric.subject.name}")
        db.session.commit()
        flash(f"Rubric '{rubric.name}' deactivated.", "success")
        return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))

    entry_count = FortnightEntry.query.filter_by(rubric_id=rubric_id).count()
    active_count = Rubric.query.filter_by(
        subject_id=rubric.subject_id, is_active=True
    ).count()
    return render_template(
        "admin/deactivate_rubric_confirm.html",
        rubric=rubric,
        entry_count=entry_count,
        is_last_active=(active_count <= 1),
    )


@admin_bp.route("/rubrics/<int:rubric_id>/reactivate", methods=["POST"])
@login_required
@require_role("admin")
def reactivate_rubric(rubric_id):
    rubric = db.session.get(Rubric, rubric_id)
    if not rubric:
        abort(404)
    rubric.is_active = True
    log_audit(user=current_user, action="edit", model_name="Rubric",
              record_id=rubric.id, field_name="is_active",
              old_value="False", new_value="True",
              note=f"Reactivated rubric '{rubric.name}' on {rubric.subject.name}")
    db.session.commit()
    flash(f"Rubric '{rubric.name}' reactivated.", "success")
    return redirect(url_for("admin.rubrics", subject_id=rubric.subject_id))
