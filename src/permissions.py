"""
Permissions layer for Classroom Pulse.

Usage in routes:
    from permissions import perms, require_role, log_audit

    @bp.route("/some-route")
    @login_required
    def view():
        p = perms()
        if not p.can_view_grade("Grade 5A"):
            abort(403)

In templates (via context_processor injected in app.py):
    {% if perms.is_admin %}...{% endif %}
"""

import datetime
from functools import wraps
from flask import abort
from flask_login import current_user
from models import db, AcademicYear, AppConfig, AuditLog, WeeklyEntry


def _active_terms():
    """Return list of Term objects for the active AcademicYear."""
    ay = AcademicYear.query.filter_by(is_active=True).first()
    return ay.terms if ay else []


def _term_for_entry(entry: WeeklyEntry):
    """Find the Term that contains this entry's week, or None."""
    for term in _active_terms():
        if term.contains_week(entry.iso_week, entry.iso_year):
            return term
    return None


class Permissions:
    """
    Computed from current_user's UserRole rows.
    Instantiated once per request via the `perms()` factory below,
    or injected into templates via context_processor.
    """

    def __init__(self, user):
        self.user = user
        if user.is_authenticated:
            self._roles = list(user.roles)          # already eager-loaded
            self._role_names = {r.role for r in self._roles}
        else:
            self._roles = []
            self._role_names = set()

    # ------------------------------------------------------------------
    # Role checks
    # ------------------------------------------------------------------

    @property
    def is_admin(self):
        return "admin" in self._role_names

    @property
    def is_coordinator(self):
        return "coordinator" in self._role_names

    @property
    def is_incharge(self):
        return "incharge" in self._role_names

    @property
    def is_teacher(self):
        return "teacher" in self._role_names

    @property
    def can_view_all(self):
        """Admin and Coordinator see everything."""
        return self.is_admin or self.is_coordinator

    # ------------------------------------------------------------------
    # Visibility helpers
    # ------------------------------------------------------------------

    def visible_grades(self):
        """
        Returns None (= no restriction) for Admin/Coordinator.
        Returns a set of grade strings the user may view for others.
        """
        if self.can_view_all:
            return None
        grades = set()
        for r in self._roles:
            if r.grade:
                grades.add(r.grade)
        return grades

    def can_view_grade(self, grade: str) -> bool:
        if self.can_view_all:
            return True
        vg = self.visible_grades()
        return vg is not None and grade in vg

    # ------------------------------------------------------------------
    # Entry helpers
    # ------------------------------------------------------------------

    def enterable_pairs(self):
        """
        Returns None (= no restriction) for Admin.
        Returns a set of (grade, subject_id) for everyone else.
        In-Charge can enter for their assigned subject in their grade,
        same as Teacher rows.
        """
        if self.is_admin:
            return None
        pairs = set()
        for r in self._roles:
            if r.role in ("teacher", "incharge", "coordinator") and r.grade and r.subject_id:
                pairs.add((r.grade, r.subject_id))
        return pairs

    def can_enter(self, grade: str, subject_id: int) -> bool:
        if self.is_admin:
            return True
        ep = self.enterable_pairs()
        return ep is not None and (grade, subject_id) in ep

    def enterable_grades(self):
        """Distinct grades where this user can enter at least one subject."""
        if self.is_admin:
            return None  # no restriction
        ep = self.enterable_pairs()
        if ep is None:
            return None
        return {grade for grade, _ in ep}

    # ------------------------------------------------------------------
    # Edit / lock checks
    # ------------------------------------------------------------------

    def grace_hours(self) -> int:
        val = AppConfig.get_value("grace_period_hours", "48")
        try:
            return int(val)
        except (TypeError, ValueError):
            return 48

    def within_grace_period(self, entry: WeeklyEntry) -> bool:
        if not entry.created_at:
            return True  # legacy entries (no timestamp) — allow
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=self.grace_hours())
        return entry.created_at >= cutoff

    def can_edit_entry(self, entry: WeeklyEntry) -> bool:
        """Can this user edit an existing WeeklyEntry?"""
        if self.is_admin:
            return True
        # Must be the original author
        if entry.created_by != self.user.id:
            return False
        # Term must not be locked
        term = _term_for_entry(entry)
        if term and term.is_locked:
            return False
        # Must be within grace period
        return self.within_grace_period(entry)

    # ------------------------------------------------------------------
    # Dashboard / at-risk visible subjects (for In-Charge)
    # ------------------------------------------------------------------

    def can_view_subject_in_grade(self, grade: str, subject_id: int) -> bool:
        """
        True if the user may view this specific subject's data.
        In-Charge sees ALL subjects in their assigned grade.
        Teacher sees only their own subjects.
        """
        if self.can_view_all:
            return True
        # In-Charge: any role=incharge for this grade → can see all subjects there
        for r in self._roles:
            if r.role == "incharge" and r.grade == grade:
                return True
        # Teacher/Coordinator: only own assignments
        return self.can_enter(grade, subject_id)

    def visible_subject_ids_for_grade(self, grade: str):
        """
        Returns None (no restriction) or a set of subject_ids visible in this grade.
        """
        if self.can_view_all:
            return None
        # In-Charge of this grade → all subjects
        for r in self._roles:
            if r.role == "incharge" and r.grade == grade:
                return None
        # Otherwise: only own enterable subjects in this grade
        return {sid for g, sid in (self.enterable_pairs() or set()) if g == grade}


def perms() -> Permissions:
    """Factory — call inside a request context."""
    return Permissions(current_user)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def require_role(*roles):
    """
    Route decorator that aborts with 403 unless the user holds at least one
    of the given role names ("admin", "coordinator", "incharge", "teacher").
    Also enforces that the user account is active.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated or not current_user.active:
                abort(401)
            p = Permissions(current_user)
            allowed = any(
                getattr(p, f"is_{role}", None) or role in p._role_names
                for role in roles
            )
            if not allowed:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

def log_audit(*, user, action: str, model_name: str, record_id: int,
              field_name: str = None, old_value=None, new_value=None, note: str = None):
    """Write one row to audit_logs and add it to the session (caller must commit)."""
    entry = AuditLog(
        user_id    = user.id,
        timestamp  = datetime.datetime.utcnow(),
        action     = action,
        model_name = model_name,
        record_id  = record_id,
        field_name = field_name,
        old_value  = str(old_value)[:200] if old_value is not None else None,
        new_value  = str(new_value)[:200] if new_value is not None else None,
        note       = note,
    )
    db.session.add(entry)
