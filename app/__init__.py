from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, send_from_directory
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from markupsafe import Markup

from .config import Config, INSTANCE_DIR, PROJECT_ROOT, UPLOADS_DIR, WEB_ROOT
from .content import get_bank, render_markdown
from .models import User, db, ensure_schema

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app() -> Flask:
    load_dotenv(WEB_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / ".env")

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_path=str(INSTANCE_DIR),
    )
    app.config.from_object(Config)
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOADS_DIR / "practice").mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Войдите, чтобы продолжить."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        if not user_id:
            return None
        return db.session.get(User, int(user_id))

    @app.template_filter("md")
    def md_filter(text: str) -> Markup:
        return Markup(render_markdown(text or ""))

    @app.template_filter("dt")
    def dt_filter(value: datetime | None) -> str:
        if not value:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        local = value.astimezone()
        return local.strftime("%d.%m.%Y %H:%M")

    @app.template_filter("duration")
    def duration_filter(seconds: int | None) -> str:
        from .progress import format_duration

        return format_duration(seconds)

    @app.context_processor
    def inject_globals():
        from .config import LEVEL_BANDS, LEVEL_LABELS
        from .ai_checker import ai_configured

        bank = get_bank()
        return {
            "LEVEL_BANDS": LEVEL_BANDS,
            "LEVEL_LABELS": LEVEL_LABELS,
            "course_title": bank.exam_title,
            "lesson_count": bank.count,
            "ai_ready": ai_configured(),
        }

    from .views.auth import bp as auth_bp
    from .views.student import bp as student_bp
    from .views.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp)

    figures_dir = Path(app.config["CONTENT_ROOT"]) / "lessons" / "figures"

    @app.route("/media/figures/<path:filename>")
    def media_figures(filename: str):
        if not current_user.is_authenticated:
            abort(401)
        return send_from_directory(figures_dir, filename)

    @app.errorhandler(403)
    def forbidden(_e):
        return (
            "<h1>Нет доступа</h1><p>Эта страница недоступна для вашей роли.</p>",
            403,
        )

    @app.route("/media/uploads/<path:filename>")
    def media_uploads(filename: str):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_admin:
            # ученик видит только свои файлы: practice/<user_id>/...
            prefix = f"practice/{current_user.id}/"
            if not filename.replace("\\", "/").startswith(prefix):
                abort(403)
        return send_from_directory(UPLOADS_DIR, filename)

    with app.app_context():
        db.create_all()
        ensure_schema()
        _ensure_bootstrap()

    return app


def _ensure_bootstrap() -> None:
    from .config import Config
    from .progress import bootstrap_student

    admin = User.query.filter_by(role="admin").first()
    if admin is None:
        admin = User(
            username=Config.ADMIN_USERNAME,
            full_name=Config.ADMIN_NAME,
            role="admin",
            level_band=None,
        )
        admin.set_password(Config.ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()

    if Config.DEMO_SEED and User.query.filter_by(role="student").count() == 0:
        demo = User(
            username=Config.DEMO_USERNAME,
            full_name=Config.DEMO_NAME,
            role="student",
            level_band=Config.DEMO_LEVEL,
        )
        demo.set_password(Config.DEMO_PASSWORD)
        db.session.add(demo)
        db.session.flush()
        bootstrap_student(demo)
        db.session.commit()
