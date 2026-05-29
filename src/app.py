import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from models import db, User

login_manager = LoginManager()


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "info"

    from auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.entry import entry_bp
    from routes.at_risk import at_risk_bp
    from routes.students import students_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(entry_bp)
    app.register_blueprint(at_risk_bp)
    app.register_blueprint(students_bp)

    with app.app_context():
        db.create_all()
        _seed_admin()

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    return app


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def _seed_admin() -> None:
    if User.query.count() == 0:
        admin = User(username="admin")
        admin.set_password("changeme123")
        db.session.add(admin)
        db.session.commit()
        print("[init] Default admin created — username: admin  password: changeme123")


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
