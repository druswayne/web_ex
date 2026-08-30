from __future__ import annotations

from pathlib import Path
import shutil

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ..config import LEVEL_BANDS, LEVEL_LABELS, UPLOADS_DIR
from ..content import HOMEWORK_SLOT_BASE, get_bank, normalize_band, render_markdown
from ..content_edit import (
    questions_from_form,
    save_practice_band,
    save_test_band,
    save_theory,
    tasks_from_form,
)
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
    sync_assignments_after_content_change,
    sync_assignments_after_level_change,
)
from . import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

MIN_PASSWORD_LEN = 6


def _delete_student(user: User) -> Path | None:
    uid = user.id
    ActivityLog.query.filter(ActivityLog.actor_id == uid).update(
        {ActivityLog.actor_id: None}, synchronize_session=False
    )
    PracticeAttempt.query.filter_by(user_id=uid).delete(synchronize_session=False)
    db.session.delete(user)
    folder = UPLOADS_DIR / "practice" / str(uid)
    return folder if folder.is_dir() else None


def _change_admin_password() -> bool:
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("new_password2") or ""
    if not current_user.check_password(current):
        flash("Текущий пароль неверный.", "danger")
        return False
    if len(new) < MIN_PASSWORD_LEN:
        flash(f"Новый пароль — не короче {MIN_PASSWORD_LEN} символов.", "warning")
        return False
    if new != confirm:
        flash("Новый пароль и подтверждение не совпадают.", "warning")
        return False
    if current_user.check_password(new):
        flash("Новый пароль совпадает с текущим.", "warning")
        return False
    current_user.set_password(new)
    log_activity(current_user.id, "password_changed", actor_id=current_user.id)
    db.session.commit()
    flash("Пароль изменён.", "success")
    return True

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
    "student_deleted": "Ученик удалён",
    "password_changed": "Пароль изменён",
    "content_theory": "Изменена теория занятия",
    "content_test": "Изменён тест занятия",
    "content_practice": "Изменена практика занятия",
}


@bp.route("/profile", methods=["GET", "POST"])
@admin_required
def profile():
    if request.method == "POST" and _change_admin_password():
        return redirect(url_for("admin.profile"))
    return render_template("admin/profile.html")


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


@bp.route("/students/<int:user_id>/delete", methods=["POST"])
@admin_required
def student_delete(user_id: int):
    user = User.query.filter_by(id=user_id, role="student").first_or_404()
    name = user.full_name
    username = user.username
    log_activity(
        current_user.id,
        "student_deleted",
        actor_id=current_user.id,
        details={"username": username, "full_name": name, "id": user.id},
    )
    upload_dir = _delete_student(user)
    db.session.commit()
    if upload_dir is not None:
        shutil.rmtree(upload_dir, ignore_errors=True)
    flash(f"Аккаунт «{name}» удалён.", "success")
    return redirect(url_for("admin.students"))


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
    if request.form.get("next") == "detail":
        return redirect(url_for("admin.lesson_detail", number=number))
    return redirect(url_for("admin.lessons"))


def _admin_lesson(number: int):
    try:
        return get_bank().get(number)
    except KeyError:
        abort(404)


def _admin_band(band: str) -> str:
    key = normalize_band(band)
    if key not in LEVEL_BANDS:
        abort(404)
    return key


@bp.route("/lessons/<int:number>")
@admin_required
def lesson_detail(number: int):
    lsn = _admin_lesson(number)
    test_counts = {b: len(lsn.test_for(b)) for b in LEVEL_BANDS}
    practice_counts = {b: len(lsn.practice.get(b) or []) for b in LEVEL_BANDS}
    return render_template(
        "admin/lesson_detail.html",
        lesson=lsn,
        test_counts=test_counts,
        practice_counts=practice_counts,
        theory_chars=len(lsn.theory_body),
    )


@bp.route("/lessons/<int:number>/theory", methods=["GET", "POST"])
@admin_required
def lesson_theory(number: int):
    lsn = _admin_lesson(number)
    if request.method == "POST":
        body = request.form.get("theory_md") or ""
        try:
            save_theory(number, body)
        except OSError:
            flash("Не удалось сохранить файл занятия.", "danger")
            return render_template("admin/lesson_theory.html", lesson=lsn, theory_md=body)
        log_activity(
            current_user.id,
            "content_theory",
            lesson_number=number,
            actor_id=current_user.id,
        )
        db.session.commit()
        flash("Теория сохранена.", "success")
        return redirect(url_for("admin.lesson_detail", number=number))
    return render_template("admin/lesson_theory.html", lesson=lsn, theory_md=lsn.theory_body)


@bp.route("/lessons/<int:number>/test")
@admin_required
def lesson_test_pick(number: int):
    lsn = _admin_lesson(number)
    counts = {b: len(lsn.test_for(b)) for b in LEVEL_BANDS}
    return render_template(
        "admin/lesson_band_pick.html",
        lesson=lsn,
        kind="test",
        kind_title="Тест",
        kind_hint="Выберите уровень, тест которого нужно изменить. Пока уровни не разделяли, у всех один и тот же набор вопросов.",
        counts=counts,
        count_unit="вопросов",
        edit_endpoint="admin.lesson_test_edit",
    )


@bp.route("/lessons/<int:number>/test/<band>", methods=["GET", "POST"])
@admin_required
def lesson_test_edit(number: int, band: str):
    lsn = _admin_lesson(number)
    band = _admin_band(band)
    if request.method == "POST":
        questions = questions_from_form(request.form)
        if not questions:
            flash("Добавьте хотя бы один вопрос.", "warning")
            return render_template(
                "admin/lesson_test_edit.html",
                lesson=lsn,
                band=band,
                questions=questions,
            )
        missing = [q.code for q in questions if not q.prompt_md or len(q.options) < 2 or not q.answers]
        if missing:
            flash(
                "У каждого вопроса нужны формулировка, минимум два варианта и хотя бы один верный ответ.",
                "warning",
            )
            return render_template(
                "admin/lesson_test_edit.html",
                lesson=lsn,
                band=band,
                questions=questions,
            )
        try:
            save_test_band(number, band, questions)
        except OSError:
            flash("Не удалось сохранить файлы теста.", "danger")
            return render_template(
                "admin/lesson_test_edit.html",
                lesson=lsn,
                band=band,
                questions=questions,
            )
        log_activity(
            current_user.id,
            "content_test",
            lesson_number=number,
            actor_id=current_user.id,
            details={"band": band, "questions": len(questions)},
        )
        db.session.commit()
        flash(f"Тест уровня {LEVEL_LABELS[band]} сохранён.", "success")
        return redirect(url_for("admin.lesson_detail", number=number))
    questions = lsn.test_for(band)
    return render_template(
        "admin/lesson_test_edit.html",
        lesson=lsn,
        band=band,
        questions=questions,
    )


@bp.route("/lessons/<int:number>/practice")
@admin_required
def lesson_practice_pick(number: int):
    lsn = _admin_lesson(number)
    counts = {b: len(lsn.practice.get(b) or []) for b in LEVEL_BANDS}
    return render_template(
        "admin/lesson_band_pick.html",
        lesson=lsn,
        kind="practice",
        kind_title="Практические задания",
        kind_hint="Выберите уровень: самостоятельная работа и домашнее задание этого блока.",
        counts=counts,
        count_unit="заданий",
        edit_endpoint="admin.lesson_practice_edit",
    )


@bp.route("/lessons/<int:number>/practice/<band>", methods=["GET", "POST"])
@admin_required
def lesson_practice_edit(number: int, band: str):
    lsn = _admin_lesson(number)
    band = _admin_band(band)
    if request.method == "POST":
        class_tasks = tasks_from_form(
            request.form, "class", kind="class", letter="C", slot_base=0
        )
        homework_tasks = tasks_from_form(
            request.form,
            "hw",
            kind="homework",
            letter="D",
            slot_base=HOMEWORK_SLOT_BASE,
        )
        if not class_tasks and not homework_tasks:
            flash("Добавьте хотя бы одно задание.", "warning")
            return render_template(
                "admin/lesson_practice_edit.html",
                lesson=lsn,
                band=band,
                class_tasks=class_tasks,
                homework_tasks=homework_tasks,
            )
        try:
            save_practice_band(number, band, class_tasks, homework_tasks)
        except OSError:
            flash("Не удалось сохранить файлы практики.", "danger")
            return render_template(
                "admin/lesson_practice_edit.html",
                lesson=lsn,
                band=band,
                class_tasks=class_tasks,
                homework_tasks=homework_tasks,
            )
        sync_assignments_after_content_change(number, band)
        log_activity(
            current_user.id,
            "content_practice",
            lesson_number=number,
            actor_id=current_user.id,
            details={
                "band": band,
                "class": len(class_tasks),
                "homework": len(homework_tasks),
            },
        )
        db.session.commit()
        flash(f"Практика уровня {LEVEL_LABELS[band]} сохранена.", "success")
        return redirect(url_for("admin.lesson_detail", number=number))
    tasks = lsn.practice.get(band) or []
    class_tasks = [t for t in tasks if t.kind != "homework"]
    homework_tasks = [t for t in tasks if t.kind == "homework"]
    return render_template(
        "admin/lesson_practice_edit.html",
        lesson=lsn,
        band=band,
        class_tasks=class_tasks,
        homework_tasks=homework_tasks,
    )


@bp.route("/preview", methods=["POST"])
@admin_required
def markdown_preview():
    source = ""
    if request.is_json:
        source = (request.get_json(silent=True) or {}).get("source") or ""
    else:
        source = request.form.get("source") or ""
    return jsonify({"html": render_markdown(source)})


@bp.route("/attempts/test/<int:attempt_id>")
@admin_required
def test_attempt(attempt_id: int):
    attempt = TestAttempt.query.get_or_404(attempt_id)
    student = db.session.get(User, attempt.user_id)
    lsn = get_bank().get(attempt.lesson_number)
    questions = {q.code: q for q in lsn.test_for(student.level_band if student else None)}
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
