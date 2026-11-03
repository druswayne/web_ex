from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, request, send_from_directory
from flask_login import LoginManager, current_user
from flask_wtf import CSRFProtect
from markupsafe import Markup

from .config import Config, INSTANCE_DIR, PROJECT_ROOT, UPLOADS_DIR, WEB_ROOT
from .content import (
    get_bank,
    lesson_number_from_video,
    render_markdown,
    video_mime,
    video_root,
)
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

    @app.template_filter("byn")
    def byn_filter(value) -> str:
        from .billing import format_byn

        return format_byn(value)

    @app.template_filter("byn_delta")
    def byn_delta_filter(value) -> str:
        from .billing import format_byn_delta

        return format_byn_delta(value)

    @app.context_processor
    def inject_globals():
        from urllib.parse import quote

        from .config import LEVEL_BANDS, LEVEL_LABELS
        from .ai_checker import ai_configured

        bank = get_bank()
        static_dir = Path(app.static_folder or "")

        def asset_v(filename: str) -> str:
            path = static_dir / filename
            try:
                return str(int(path.stat().st_mtime))
            except OSError:
                return "1"

        configured = (app.config.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
        handle = (app.config.get("ADMIN_TELEGRAM") or "").strip().lstrip("@")
        topup_url = ""
        if handle:
            login = current_user.username if current_user.is_authenticated else ""
            note = "Здравствуйте! Хочу пополнить баланс."
            if login:
                note += f" Логин: {login}"
            topup_url = f"https://t.me/{handle}?text={quote(note)}"
        support_new = 0
        if current_user.is_authenticated and current_user.is_admin:
            from .models import SupportMessage

            support_new = SupportMessage.query.filter(SupportMessage.read_at.is_(None)).count()
        return {
            "LEVEL_BANDS": LEVEL_BANDS,
            "LEVEL_LABELS": LEVEL_LABELS,
            "course_title": bank.exam_title,
            "lesson_count": bank.count,
            "ai_ready": ai_configured(),
            "site_base": configured or request.url_root.rstrip("/"),
            "seo_google": app.config.get("GOOGLE_SITE_VERIFICATION") or "",
            "seo_yandex": app.config.get("YANDEX_VERIFICATION") or "",
            "seo_bing": app.config.get("BING_SITE_VERIFICATION") or "",
            "seo_facebook": app.config.get("FACEBOOK_DOMAIN_VERIFICATION") or "",
            "topup_url": topup_url,
            "contact_email": app.config.get("CONTACT_EMAIL") or "",
            "support_new": support_new,
            "asset_v": asset_v,
        }

    from .views.auth import bp as auth_bp
    from .views.student import bp as student_bp
    from .views.admin import bp as admin_bp
    from .views.seo import bp as seo_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(seo_bp)

    figures_dir = Path(app.config["CONTENT_ROOT"]) / "lessons" / "figures"
    videos_dir = video_root(Path(app.config["CONTENT_ROOT"]))

    @app.route("/media/figures/<path:filename>")
    def media_figures(filename: str):
        if not current_user.is_authenticated:
            abort(401)
        return send_from_directory(figures_dir, filename)

    @app.route("/media/video/<path:filename>")
    def media_video(filename: str):
        if not current_user.is_authenticated:
            abort(401)
        name = Path(filename).name
        number = lesson_number_from_video(name)
        if number is None:
            abort(404)
        if not current_user.is_admin:
            from .progress import is_unlocked

            if not current_user.is_student or not current_user.is_active:
                abort(403)
            if not is_unlocked(current_user, number):
                abort(403)
        if not (videos_dir / name).is_file():
            abort(404)
        return send_from_directory(
            videos_dir,
            name,
            mimetype=video_mime(name),
        )

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
        resp = send_from_directory(UPLOADS_DIR, filename)
        resp.cache_control.public = False
        resp.cache_control.private = True
        resp.cache_control.max_age = 7 * 24 * 3600
        return resp

    with app.app_context():
        db.create_all()
        ensure_schema()
        _ensure_bootstrap()
        from .tutor_jobs import resume_pending_tutor

        resume_pending_tutor(app)
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
            from .check_jobs import start_practice_watch

            start_practice_watch(app)

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
            balance_byn=0,
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
