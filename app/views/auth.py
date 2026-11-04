from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from ..accounts import email_error, normalize_email, password_error, username_error
from ..config import LEVEL_BANDS, Config
from ..models import User, db, utcnow
from ..progress import bootstrap_student, log_activity

bp = Blueprint("auth", __name__)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("student.dashboard"))
    return render_template("login.html", error=None, is_home=True)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.index"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.find_by_username(username)
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            user.last_login_at = utcnow()
            log_activity(user.id, "login")
            db.session.commit()
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(url_for("auth.index"))
        error = "Неверный логин или пароль."
    return render_template("login.html", error=error, is_home=False)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("auth.index"))
    form = {
        "username": "",
        "email": "",
        "level_band": "1-2",
    }
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        level_band = request.form.get("level_band") or "1-2"
        form = {
            "username": username,
            "email": email,
            "level_band": level_band,
        }
        error = (
            username_error(username)
            or email_error(email)
            or password_error(password, username=username, email=email)
        )
        if error is None and password != password2:
            error = "Пароль и подтверждение не совпадают."
        if error is None and level_band not in LEVEL_BANDS:
            error = "Выберите уровень заданий."
        if error is None and User.username_taken(username):
            error = "Такой логин уже занят."
        if error is None and User.email_taken(email):
            error = "Такая почта уже используется."
        if error is None:
            user = User(
                username=username,
                full_name=username,
                email=normalize_email(email),
                role="student",
                level_band=level_band,
                is_active=True,
                balance_byn=Config.STARTING_BALANCE_BYN,
            )
            user.set_password(password)
            user.last_login_at = utcnow()
            db.session.add(user)
            db.session.flush()
            bootstrap_student(user)
            log_activity(user.id, "registered", actor_id=user.id, details={"level": level_band})
            log_activity(user.id, "login")
            db.session.commit()
            login_user(user, remember=True)
            flash("Аккаунт создан. Первое занятие уже открыто.", "success")
            return redirect(url_for("student.dashboard"))
    return render_template("register.html", error=error, form=form)


@bp.route("/privacy")
def privacy():
    return render_template(
        "privacy.html",
        admin_telegram=(current_app.config.get("ADMIN_TELEGRAM") or "").strip().lstrip("@"),
    )


@bp.route("/cookies")
def cookies():
    return render_template(
        "cookies.html",
        admin_telegram=(current_app.config.get("ADMIN_TELEGRAM") or "").strip().lstrip("@"),
    )


@bp.route("/logout")
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("auth.login"))
