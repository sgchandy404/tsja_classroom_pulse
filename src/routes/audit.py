"""
Audit log viewer.
Coordinators: read-only.  Admins: read-only (logs are never deleted by anyone).
"""
import datetime
from flask import Blueprint, render_template, request
from flask_login import login_required
from models import db, AuditLog, User, WeeklyEntry, AcademicYear, Term
from permissions import require_role

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

PAGE_SIZE = 50


def _current_week_monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _parse_date(s: str):
    """Accept DD/MM/YYYY or YYYY-MM-DD; return datetime or None."""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _describe(log: AuditLog) -> str:
    """Build a human-readable one-line description for an audit log entry."""
    m   = log.model_name
    a   = log.action
    f   = log.field_name or ""
    old = log.old_value or ""
    new = log.new_value or ""

    # ── WeeklyEntry ──────────────────────────────────────────────────────────
    if m == "WeeklyEntry":
        entry = db.session.get(WeeklyEntry, log.record_id)
        if entry:
            student = entry.student.name
            subject = entry.subject.name
            week_lbl = f"week {entry.iso_week}/{entry.iso_year}"
        else:
            student = subject = f"#{log.record_id}"
            week_lbl = ""
        if a == "create":
            return f"Recorded {student} · {subject} as '{new}'" + (f" ({week_lbl})" if week_lbl else "")
        else:
            return f"Changed {student} · {subject}: '{old}' → '{new}'" + (f" ({week_lbl})" if week_lbl else "")

    # ── User ─────────────────────────────────────────────────────────────────
    if m == "User":
        target = db.session.get(User, log.record_id)
        name = f"'{target.username}'" if target else f"#{log.record_id}"
        if a == "create":
            return f"Created user {name}"
        if f == "active":
            state = "activated" if new == "True" else "deactivated"
            return f"User {name} {state}"
        if f == "password":
            return f"Password changed for user {name}"
        if f == "roles":
            return f"Roles updated for user {name} — {new}"

    # ── AcademicYear ─────────────────────────────────────────────────────────
    if m == "AcademicYear":
        if a == "create":
            return f"Created academic year {new}"
        if a == "delete":
            return f"Deleted academic year {old}"
        if f == "is_active":
            return f"Set {new} as the active academic year"
        if f == "terms":
            ay = db.session.get(AcademicYear, log.record_id)
            label = ay.label if ay else f"#{log.record_id}"
            return f"Updated term boundaries for {label}"

    # ── Term ─────────────────────────────────────────────────────────────────
    if m == "Term":
        note = log.note or f"Term #{log.record_id}"
        return f"{note} {new}"

    # ── AppConfig ─────────────────────────────────────────────────────────────
    if m == "AppConfig":
        if f == "grace_period_hours":
            return f"Grace period changed: {old}h → {new}h"

    # ── Fallback ──────────────────────────────────────────────────────────────
    parts = [f"{a} {m} #{log.record_id}"]
    if f:
        parts.append(f"· {f}")
    if old:
        parts.append(f": '{old}' → '{new}'")
    elif new:
        parts.append(f"→ '{new}'")
    return " ".join(parts)


@audit_bp.route("/")
@login_required
@require_role("admin", "coordinator")
def index():
    page    = request.args.get("page", 1, type=int)
    user_id = request.args.get("user_id", "", type=str)
    action  = request.args.get("action", "")

    # Default date_from to Monday of the current ISO week (DD/MM/YYYY display)
    default_from = _current_week_monday().strftime("%d/%m/%Y")
    date_from = request.args.get("date_from", default_from)
    date_to   = request.args.get("date_to", "")

    q = AuditLog.query.order_by(AuditLog.timestamp.desc())

    if user_id:
        q = q.filter(AuditLog.user_id == int(user_id))
    if action:
        q = q.filter(AuditLog.action == action)
    if date_from:
        dt = _parse_date(date_from)
        if dt:
            q = q.filter(AuditLog.timestamp >= dt)
    if date_to:
        dt = _parse_date(date_to)
        if dt:
            q = q.filter(AuditLog.timestamp <= dt.replace(hour=23, minute=59, second=59))

    pagination = q.paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    users = User.query.order_by(User.username).all()

    # Pre-compute descriptions so the template stays clean
    log_rows = [
        {"log": log, "description": _describe(log)}
        for log in pagination.items
    ]

    return render_template(
        "audit/index.html",
        log_rows=log_rows,
        pagination=pagination,
        users=users,
        sel_user_id=user_id,
        sel_action=action,
        sel_date_from=date_from,
        sel_date_to=date_to,
    )
