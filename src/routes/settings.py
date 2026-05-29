"""
Settings blueprint — manage academic years and terms.
Admin-only.
"""
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from models import db, AcademicYear, Term
from permissions import require_role

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_terms(start_year: int) -> list[dict]:
    """
    Return default term boundaries for an Indian Cambridge-affiliated school.

    Term 1 — April → September  (ISO weeks ~14–39 of start_year)
    Term 2 — October → December (ISO weeks ~40–52 of start_year)
    Term 3 — January → March    (ISO weeks ~1–13  of start_year+1)
    """
    end_year = start_year + 1

    def _w(year, month, day):
        iso = datetime.date(year, month, day).isocalendar()
        return iso.week, iso.year

    t1_start = _w(start_year, 4,  1)
    t1_end   = _w(start_year, 9, 30)
    t2_start = _w(start_year, 10, 1)
    t2_end   = _w(start_year, 12, 31)
    t3_start = _w(end_year,   1,  1)
    t3_end   = _w(end_year,   3, 31)

    return [
        {"name": "Term 1", "start_iso_week": t1_start[0], "start_iso_year": t1_start[1],
                           "end_iso_week":   t1_end[0],   "end_iso_year":   t1_end[1]},
        {"name": "Term 2", "start_iso_week": t2_start[0], "start_iso_year": t2_start[1],
                           "end_iso_week":   t2_end[0],   "end_iso_year":   t2_end[1]},
        {"name": "Term 3", "start_iso_week": t3_start[0], "start_iso_year": t3_start[1],
                           "end_iso_week":   t3_end[0],   "end_iso_year":   t3_end[1]},
    ]


def _iso_to_date_str(iso_week, iso_year, day=1):
    """Return 'YYYY-MM-DD' for the given ISO week (day 1=Mon … 7=Sun)."""
    try:
        return datetime.date.fromisocalendar(iso_year, iso_week, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _date_str_to_iso(date_str: str):
    """Parse 'YYYY-MM-DD' → (iso_week, iso_year). Raises ValueError on bad input."""
    d = datetime.date.fromisoformat(date_str)
    iso = d.isocalendar()
    return iso.week, iso.year


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
        db.session.flush()  # get ay.id

        for td in _default_terms(start_year):
            db.session.add(Term(academic_year_id=ay.id, **td))

        db.session.commit()
        flash(f"Academic year {label} created with default term boundaries.", "success")
        return redirect(url_for("settings.edit_year", year_id=ay.id))

    # Suggest next year after the latest existing one
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
            db.session.commit()
            flash(f"{ay.label} is now the active academic year.", "success")
            return redirect(url_for("settings.index"))

        if action == "save_terms":
            errors = []
            for term in ay.terms:
                start_key = f"start_{term.id}"
                end_key   = f"end_{term.id}"
                try:
                    sw, sy = _date_str_to_iso(request.form[start_key])
                    ew, ey = _date_str_to_iso(request.form[end_key])
                    if (sy, sw) >= (ey, ew):
                        errors.append(f"{term.name}: start must be before end.")
                        continue
                    term.start_iso_week = sw
                    term.start_iso_year = sy
                    term.end_iso_week   = ew
                    term.end_iso_year   = ey
                except (ValueError, KeyError) as exc:
                    errors.append(f"{term.name}: invalid date — {exc}")

            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                db.session.commit()
                flash("Term boundaries saved.", "success")

            return redirect(url_for("settings.edit_year", year_id=year_id))

        if action == "toggle_lock":
            term_id = request.form.get("term_id", type=int)
            term = Term.query.get(term_id)
            if term and term.academic_year_id == ay.id:
                term.is_locked = not term.is_locked
                db.session.commit()
                state = "locked" if term.is_locked else "unlocked"
                flash(f"{term.name} {state}.", "success")
            return redirect(url_for("settings.edit_year", year_id=year_id))

        if action == "delete_year":
            label = ay.label
            db.session.delete(ay)
            db.session.commit()
            flash(f"Academic year {label} deleted.", "success")
            return redirect(url_for("settings.index"))

    # Build a list of (term, start_date_str, end_date_str) for the form
    term_rows = [
        {
            "term":      t,
            "start_str": _iso_to_date_str(t.start_iso_week, t.start_iso_year, day=1),
            "end_str":   _iso_to_date_str(t.end_iso_week,   t.end_iso_year,   day=7),
        }
        for t in ay.terms
    ]

    return render_template("settings/edit_year.html", ay=ay, term_rows=term_rows)
