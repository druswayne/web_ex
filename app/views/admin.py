from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..config import LEVEL_BANDS, LEVEL_LABELS
from ..content import get_bank, render_markdown
from ..models import (
    ActivityLog,
    LessonAccess,
    LessonProgress,
    PracticeAssignment,
    PracticeAttempt,
    TestAttempt,
    User,
    db,
)
from ..progress import (
    bootstrap_student,
    lesson_state,
    log_activity,
    refresh_practice_progress,
    set_access,
    sync_assignments_after_level_change,
)
from . import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

ACTION_LABELS = {
    "login": "Вход на сайт",
    "lesson_open": "Открыл занятие",
    "theory_open": "Открыл теорию",
    "theory_complete": "Дочитал теорию",
    "test_start": "Начал тест",
    "test_submit": "Сдал тест",
    "practice_open": "Открыл практическую часть",
    "practice_submit": "Отправил решение",
    "practice_queued": "Отправил решение на проверку",
    "practice_admin_credit": "Учитель засчитал задание",
    "practice_admin_uncredit": "Учитель снял зачёт задания",
    "lesson_unlocked": "Занятие открыто",
    "lesson_locked": "Занятие закрыто",
    "lesson_complete": "Занятие пройдено",
    "achievement": "Получено достижение",
    "profile_updated": "Профиль изменён",
    "student_created": "Ученик создан",
}


@bp.route("/")
@admin_required
def dashboard():
    bank = get_bank()
    students = User.query.filter_by(role="student").order_by(User.full_name).all()
    cards = []
    for s in students:
        states = [lesson_state(s, lsn.number) for lsn in bank.all()]
        done = sum(1 for st in states if st["practice"])
        cards.append(
            {
                "user": s,
                "done": done,
                "total": bank.count,
                "pct": round(100 * done / bank.count) if bank.count else 0,
                "last": s.last_login_at,
            }
        )
    recent = (
        ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(20).all()
    )
    users = {u.id: u for u in User.query.all()}
    return render_template(
        "admin/dashboard.html",
        cards=cards,
        recent=recent,
        users=users,
        action_labels=ACTION_LABELS,
        student_count=len(students),
    )


@bp.route("/students")
@admin_required
def students():
    q = (request.args.get("q") or "").strip()
    query = User.query.filter_by(role="student")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.full_name.ilike(like), User.username.ilike(like)))
    rows = query.order_by(User.full_name).all()
    bank = get_bank()
    data = []
    for s in rows:
        done = LessonProgress.query.filter(
            LessonProgress.user_id == s.id, LessonProgress.completed_at.isnot(None)
        ).count()
        data.append({"user": s, "done": done, "total": bank.count})
    return render_template("admin/students.html", rows=data, q=q)


@bp.route("/students/new", methods=["GET", "POST"])
@admin_required
def student_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        full_name = (request.form.get("full_name") or "").strip()
        level_band = request.form.get("level_band") or "1-2"
        if not username or not password or not full_name:
            flash("Заполните логин, пароль и имя.", "warning")
            return render_template("admin/student_form.html", user=None, level_band=level_band)
        if User.username_taken(username):
            flash("Такой логин уже занят.", "danger")
            return render_template("admin/student_form.html", user=None, level_band=level_band)
        if level_band not in LEVEL_BANDS:
            level_band = "1-2"
        user = User(
            username=username,
            full_name=full_name,
            role="student",
            level_band=level_band,
            is_active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        bootstrap_student(user)
        log_activity(user.id, "student_created", actor_id=current_user.id, details={"level": level_band})
        db.session.commit()
        flash("Ученик создан. Первое занятие уже открыто.", "success")
        return redirect(url_for("admin.student_detail", user_id=user.id))
    return render_template("admin/student_form.html", user=None, level_band="3-4")


@bp.route("/students/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def student_edit(user_id: int):
    user = User.query.filter_by(id=user_id, role="student").first_or_404()
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        level_band = request.form.get("level_band") or user.level_band
        is_active = request.form.get("is_active") == "1"
        if not full_name or not username:
            flash("Имя и логин обязательны.", "warning")
            return render_template("admin/student_form.html", user=user, level_band=level_band)
        if User.username_taken(username, exclude_id=user.id):
            flash("Такой логин уже занят.", "danger")
            return render_template("admin/student_form.html", user=user, level_band=level_band)
        old_level = user.level_band
        user.full_name = full_name
        user.username = username
        user.is_active = is_active
        if level_band in LEVEL_BANDS:
            user.level_band = level_band
        if password:
            user.set_password(password)
        if old_level != user.level_band:
            sync_assignments_after_level_change(user, user.level_band)
        log_activity(
            user.id,
            "profile_updated",
            actor_id=current_user.id,
            details={"old_level": old_level, "new_level": user.level_band},
        )
        db.session.commit()
        flash("Профиль сохранён.", "success")
        return redirect(url_for("admin.student_detail", user_id=user.id))
    return render_template("admin/student_form.html", user=user, level_band=user.level_band)


@bp.route("/students/<int:user_id>")
@admin_required
def student_detail(user_id: int):
    user = User.query.filter_by(id=user_id, role="student").first_or_404()
    bank = get_bank()
    lessons = []
    for lsn in bank.all():
        st = lesson_state(user, lsn.number)
        tests = (
            TestAttempt.query.filter_by(user_id=user.id, lesson_number=lsn.number)
            .order_by(TestAttempt.created_at.desc())
            .all()
        )
        assignments = (
            PracticeAssignment.query.filter_by(user_id=user.id, lesson_number=lsn.number)
            .order_by(PracticeAssignment.is_active.desc(), PracticeAssignment.slot)
            .all()
        )
        lessons.append(
            {
                "lesson": lsn,
                "state": st,
                "tests": tests,
                "assignments": assignments,
            }
        )
    activities = (
        ActivityLog.query.filter_by(user_id=user.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(80)
        .all()
    )
    return render_template(
        "admin/student_detail.html",
        student=user,
        lessons=lessons,
        activities=activities,
        action_labels=ACTION_LABELS,
        level_label=LEVEL_LABELS.get(user.level_band or "", user.level_band),
    )


@bp.route("/students/<int:user_id>/access", methods=["POST"])
@admin_required
def student_access(user_id: int):
    user = User.query.filter_by(id=user_id, role="student").first_or_404()
    number = int(request.form.get("lesson_number") or 0)
    unlocked = request.form.get("unlocked") == "1"
    if number:
        set_access(user.id, number, unlocked, source="admin", actor_id=current_user.id)
        db.session.commit()
        flash(
            f"Занятие {number} {'открыто' if unlocked else 'закрыто'} для {user.full_name}.",
            "success",
        )
    return redirect(url_for("admin.student_detail", user_id=user.id))


@bp.route("/lessons")
@admin_required
def lessons():
    bank = get_bank()
    students = User.query.filter_by(role="student").all()
    rows = []
    for lsn in bank.all():
        open_n = LessonAccess.query.filter_by(lesson_number=lsn.number, unlocked=True).count()
        if lsn.number == 1:
            open_n = max(open_n, len(students))
        rows.append({"lesson": lsn, "open_n": open_n, "total": len(students)})
    return render_template("admin/lessons.html", rows=rows)


@bp.route("/lessons/<int:number>/access", methods=["POST"])
@admin_required
def lessons_access(number: int):
    action = request.form.get("action")
    students = User.query.filter_by(role="student").all()
    unlocked = action == "open"
    for s in students:
        set_access(s.id, number, unlocked, source="admin", actor_id=current_user.id)
    db.session.commit()
    flash(
        f"Занятие {number} {'открыто' if unlocked else 'закрыто'} для всех учеников.",
        "success",
    )
    return redirect(url_for("admin.lessons"))


@bp.route("/attempts/test/<int:attempt_id>")
@admin_required
def test_attempt(attempt_id: int):
    attempt = TestAttempt.query.get_or_404(attempt_id)
    student = db.session.get(User, attempt.user_id)
    lsn = get_bank().get(attempt.lesson_number)
    questions = {q.code: q for q in lsn.test}
    rows = []
    for item in attempt.details or []:
        q = questions.get(item.get("code"))
        rows.append(
            {
                "item": item,
                "prompt": render_markdown(q.prompt_md) if q else "",
                "options": q.options if q else [],
            }
        )
    return render_template(
        "admin/test_attempt.html",
        attempt=attempt,
        student=student,
        lesson=lsn,
        rows=rows,
    )


@bp.route("/attempts/practice/<int:assignment_id>")
@admin_required
def practice_attempts(assignment_id: int):
    assignment = PracticeAssignment.query.get_or_404(assignment_id)
    student = db.session.get(User, assignment.user_id)
    lsn = get_bank().get(assignment.lesson_number)
    attempts = (
        PracticeAttempt.query.filter_by(assignment_id=assignment.id)
        .order_by(PracticeAttempt.created_at.desc())
        .all()
    )
    return render_template(
        "admin/practice_attempts.html",
        assignment=assignment,
        student=student,
        lesson=lsn,
        attempts=attempts,
        task_html=render_markdown(f"**{assignment.title}**\n\n{assignment.task_md}"),
    )


@bp.route("/attempts/practice/<int:assignment_id>/credit", methods=["POST"])
@admin_required
def practice_credit(assignment_id: int):
    assignment = PracticeAssignment.query.get_or_404(assignment_id)
    student = db.session.get(User, assignment.user_id)
    credit = request.form.get("credit") == "1"
    if credit:
        assignment.is_correct = True
        latest = (
            PracticeAttempt.query.filter_by(assignment_id=assignment.id)
            .filter(PracticeAttempt.ai_verdict != "pending")
            .order_by(PracticeAttempt.created_at.desc())
            .first()
        )
        if latest is not None:
            latest.is_correct = True
            raw = dict(latest.ai_raw) if isinstance(latest.ai_raw, dict) else {}
            raw["admin_override"] = True
            raw["admin_override_by"] = current_user.id
            latest.ai_raw = raw
            note = "Учитель засчитал это решение."
            if note not in (latest.ai_feedback or ""):
                extra = f"\n\n{note}" if (latest.ai_feedback or "").strip() else note
                latest.ai_feedback = (latest.ai_feedback or "") + extra
        log_activity(
            assignment.user_id,
            "practice_admin_credit",
            lesson_number=assignment.lesson_number,
            details={"task": assignment.task_code},
            actor_id=current_user.id,
        )
        flash(f"Задание {assignment.task_code} засчитано для {student.full_name}.", "success")
    else:
        assignment.is_correct = False
        for att in PracticeAttempt.query.filter_by(assignment_id=assignment.id).all():
            raw = att.ai_raw if isinstance(att.ai_raw, dict) else {}
            if raw.get("admin_override"):
                att.is_correct = att.ai_verdict == "correct"
                raw = dict(raw)
                raw.pop("admin_override", None)
                raw.pop("admin_override_by", None)
                att.ai_raw = raw
                att.ai_feedback = (att.ai_feedback or "").replace(
                    "\n\nУчитель засчитал это решение.", ""
                ).replace("Учитель засчитал это решение.", "").strip()
        log_activity(
            assignment.user_id,
            "practice_admin_uncredit",
            lesson_number=assignment.lesson_number,
            details={"task": assignment.task_code},
            actor_id=current_user.id,
        )
        flash(f"Зачёт задания {assignment.task_code} снят.", "info")
    if student is not None:
        refresh_practice_progress(student, assignment.lesson_number)
    db.session.commit()
    return redirect(url_for("admin.practice_attempts", assignment_id=assignment.id))
