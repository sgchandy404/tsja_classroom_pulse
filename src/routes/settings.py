"""
Settings blueprint — manage academic years and terms.
Admin-only.
"""
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, AcademicYear, Term
from permissions import require_role, log_audit

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_terms(start_year: int) -> list[dict]:
    """Return default term boundaries (proper Date columns)."""
    end_year = start_year + 1
    return [
        {"name": "Term 1",
         "start_date": datetime.date(start_year, 4, 1),
         "end_date":   datetime.date(start_year, 9, 30)},
        {"name": "Term 2",
         "start_date": datetime.date(start_year, 10, 1),
         "end_date":   datetime.date(start_year, 12, 31)},
        {"name": "Term 3",
         "start_date": datetime.date(end_year, 1, 1),
         "end_date":   datetime.date(end_year, 3, 31)},
    ]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@settings_bp.route("/")
@login_required
@require_role("admin")
def index():
    years = AcademicYear.query.order_by(AcademicYear.start_year.desc()).all()
    return render_template("settings/index.html", years=years)


@settings_bp.route("/years/new", methods=["GET", "POST"])
@login_required
@require_role("admin")
def new_year():
    if request.method == "POST":
        start_year = int(request.form["start_year"])

        if AcademicYear.query.filter_by(start_year=start_year).first():
            flash(f"Academic year starting {start_year} already exists.", "error")
            return redirect(url_for("settings.new_year"))

        label = f"{start_year}–{str(start_year + 1)[-2:]}"
        ay = AcademicYear(label=label, start_year=start_year, is_active=False)
        db.session.add(ay)
        db.session.flush()

        for td in _default_terms(start_year):
            db.session.add(Term(academic_year_id=ay.id, **td))

        log_audit(user=current_user, action="create", model_name="AcademicYear",
                  record_id=ay.id, field_name="label", new_value=label)
        db.session.commit()
        flash(f"Academic year {label} created with default term boundaries.", "success")
        return redirect(url_for("settings.edit_year", year_id=ay.id))

    latest = AcademicYear.query.order_by(AcademicYear.start_year.desc()).first()
    suggested = (latest.start_year + 1) if latest else datetime.date.today().year
    return render_template("settings/new_year.html", suggested=suggested)


@settings_bp.route("/years/<int:year_id>", methods=["GET", "POST"])
@login_required
@require_role("admin")
def edit_year(year_id):
    ay = AcademicYear.query.get_or_404(year_id)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "set_active":
            AcademicYear.query.update({"is_active": False})
            ay.is_active = True
            log_audit(user=current_user, action="edit", model_name="AcademicYear",
                      record_id=ay.id, field_name="is_active", new_value=ay.label,
                      note="Set as active year")
            db.session.commit()
            flash(f"{ay.label} is now the active academic year.", "success")
            return redirect(url_for("settings.index"))

        if action == "save_terms":
            errors = []
            for term in ay.terms:
                start_key = f"start_{term.id}"
                end_key   = f"end_{term.id}"
                try:
                    sd = datetime.date.fromisoformat(request.form[start_key])
                    ed = datetime.date.fromisoformat(request.form[end_key])
                    if sd >= ed:
                        errors.append(f"{term.name}: start must be before end.")
                        continue
                    term.start_date = sd
                    term.end_date   = ed
                except (ValueError, KeyError) as exc:
                    errors.append(f"{term.name}: invalid date — {exc}")

            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                log_audit(user=current_user, action="edit", model_name="AcademicYear",
                          record_id=ay.id, field_name="terms", new_value="boundaries updated")
                db.session.commit()
                flash("Term boundaries saved.", "success")

            return redirect(url_for("settings.edit_year", year_id=year_id))

        if action == "toggle_lock":
            term_id = request.form.get("term_id", type=int)
            term = db.session.get(Term, term_id)
            if term and term.academic_year_id == ay.id:
                term.is_locked = not term.is_locked
                state = "locked" if term.is_locked else "unlocked"
                log_audit(user=current_user, action="edit", model_name="Term",
                          record_id=term.id, field_name="is_locked",
                          new_value=state, note=f"{ay.label} {term.name}")
                db.session.commit()
                flash(f"{term.name} {state}.", "success")
            return redirect(url_for("settings.edit_year", year_id=year_id))

        if action == "delete_year":
            label = ay.label
            log_audit(user=current_user, action="delete", model_name="AcademicYear",
                      record_id=ay.id, old_value=label, note="Academic year deleted")
            db.session.delete(ay)
            db.session.commit()
            flash(f"Academic year {label} deleted.", "success")
            return redirect(url_for("settings.index"))

    term_rows = [
        {
            "term":      t,
            "start_str": t.start_date.strftime("%Y-%m-%d") if t.start_date else "",
            "end_str":   t.end_date.strftime("%Y-%m-%d")   if t.end_date   else "",
        }
        for t in ay.terms
    ]

    return render_template("settings/edit_year.html", ay=ay, term_rows=term_rows)
