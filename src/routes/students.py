from flask import Blueprint, render_template
from flask_login import login_required
from models import Student

students_bp = Blueprint("students", __name__, url_prefix="/students")


@students_bp.route("/<int:student_id>")
@login_required
def detail(student_id):
    student = Student.query.get_or_404(student_id)
    return render_template("students/detail.html", student=student)
