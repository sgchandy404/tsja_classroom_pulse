"""
Admin blueprint — user management, grade/subject management, app config.
All routes require the 'admin' role.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required
from models import db, User, UserRole, Subject, Student, AppConfig, ROLES
from permissions import require_role

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_grades():
    grades = [r[0] for r in db.session.query(Student.grade).distinct().order_by(Student.grade).all()]
    # Also include grades that only have subjects (no students yet)
    subj_grades = [r[0] for r in db.session.query(Subject.grade).distinct().order_by(Subject.grade).all()]
    return sorted(set(grades) | set(subj_grades))


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
        g: Subject.query.filter_by(grade=g).order_by(Subject.name).all()
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
        g: Subject.query.filter_by(grade=g).order_by(Subject.name).all()
        for g in grades
    }

    if request.method == "POST":
        action = request.form.get("action")

        if action == "toggle_active":
            if user.username == "admin" and user.active:
                flash("Cannot deactivate the primary admin account.", "error")
            else:
                user.active = not user.active
                db.session.commit()
                state = "activated" if user.active else "deactivated"
                flash(f"User '{user.username}' {state}.", "success")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        if action == "change_password":
            pw = request.form.get("new_password", "").strip()
            if len(pw) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                user.set_password(pw)
                db.session.commit()
                flash("Password updated.", "success")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        if action == "save_roles":
            _save_roles(user, request.form)
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
            AppConfig.set_value("grace_period_hours", hours)
            db.session.commit()
            flash(f"Grace period set to {hours} hours.", "success")
        except ValueError:
            flash("Grace period must be a non-negative integer.", "error")
        return redirect(url_for("admin.config"))

    grace = AppConfig.get_value("grace_period_hours", "48")
    return render_template("admin/config.html", grace_period_hours=grace)
