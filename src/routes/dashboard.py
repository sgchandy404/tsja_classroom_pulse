from flask import Blueprint, render_template

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


# Placeholder — full implementation in Phase 4
@dashboard_bp.route("/")
def index():
    return render_template("dashboard/index.html")
