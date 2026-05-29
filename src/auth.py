from flask import Blueprint

auth_bp = Blueprint("auth", __name__)


# Placeholder — full implementation in Phase 2
@auth_bp.route("/login")
def login():
    return "Login coming in Phase 2", 200


@auth_bp.route("/logout")
def logout():
    return "Logout coming in Phase 2", 200
