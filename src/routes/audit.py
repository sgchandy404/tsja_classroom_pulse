"""
Audit log viewer.
Coordinators: read-only.  Admins: read-only (logs are never deleted by anyone).
"""
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, AuditLog, User
from permissions import require_role

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

PAGE_SIZE = 50


@audit_bp.route("/")
@login_required
@require_role("admin", "coordinator")
def index():
    page     = request.args.get("page", 1, type=int)
    user_id  = request.args.get("user_id", "", type=str)
    action   = request.args.get("action", "")
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")

    q = AuditLog.query.order_by(AuditLog.timestamp.desc())

    if user_id:
        q = q.filter(AuditLog.user_id == int(user_id))
    if action:
        q = q.filter(AuditLog.action == action)
    if date_from:
        try:
            import datetime
            q = q.filter(AuditLog.timestamp >= datetime.datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            import datetime
            q = q.filter(AuditLog.timestamp <= datetime.datetime.fromisoformat(date_to + "T23:59:59"))
        except ValueError:
            pass

    pagination = q.paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    users = User.query.order_by(User.username).all()

    return render_template(
        "audit/index.html",
        logs=pagination.items,
        pagination=pagination,
        users=users,
        sel_user_id=user_id,
        sel_action=action,
        sel_date_from=date_from,
        sel_date_to=date_to,
    )
