from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))

        flash("Invalid username or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        pw = request.form.get("password", "").strip()
        pw2 = request.form.get("password2", "").strip()
        if len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            current_user.set_password(pw)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password updated. Welcome to Classroom Pulse.", "success")
            return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
