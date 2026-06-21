"""
Admin blueprint — user management, grade/subject management, app config.
All routes require the 'admin' role.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db, User, UserRole, Subject, Student, Grade, Rubric, AppConfig, FortnightEntry, ROLES
from permissions import require_role, log_audit, perms as get_perms

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
