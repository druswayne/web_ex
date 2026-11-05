from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from ..ai_checker import (
    ai_configured,
    prepare_photo,
)
from ..billing import (
    affordable,
    estimate_messages_tokens,
    format_byn,
    insufficient_message,
    quote_tokens,
)
from ..check_jobs import (
    PENDING,
    pending_assignment_ids,
    start_practice_check,
    sweep_stale_pending,
)
from ..accounts import email_error, normalize_email, password_error
from ..config import LEVEL_BANDS, UPLOADS_DIR
from ..content import display_prompt, find_lesson_video, get_bank, render_markdown
from ..models import (
    PracticeAttempt,
    SUPPORT_KINDS,
    SupportMessage,
    TestAttempt,
    TheoryChatMessage,
    User,
    db,
    utcnow,
)
from ..progress import (
    active_assignments,
    ensure_assignments,
    is_unlocked,
    lesson_state,
    lesson_success_map,
    log_activity,
    mark_theory_progress,
    mark_theory_video,
    record_test_attempt,
    refresh_practice_progress,
    sync_assignments_after_level_change,
    test_done,
    theory_done,
)
from ..theory_tutor import (
    MAX_QUESTION,
    build_tutor_messages,
    format_message_html,
    tutor_completion_cap,
)
from ..tutor_jobs import (
    PENDING as TUTOR_PENDING,
    PLACEHOLDER as TUTOR_PLACEHOLDER,
    pending_tutor_message,
    start_tutor_reply,
    sweep_stale_tutor,
)
from . import student_required

bp = Blueprint("student", __name__)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _lesson(number: int):
    try:
        return get_bank().get(number)
    except KeyError:
        abort(404)


def _states():
    bank = get_bank()
    return {lsn.number: lesson_state(current_user, lsn.number) for lsn in bank.all()}


@bp.route("/cabinet")
@student_required
def dashboard():
    bank = get_bank()
    states = _states()
    progresses = [s["progress"] for s in states.values()]
    done = sum(1 for s in states.values() if s["practice"])
    unlocked = sum(1 for s in states.values() if s["unlocked"])
    tests = TestAttempt.query.filter_by(user_id=current_user.id).all()
    avg_test = round(sum(t.score for t in tests) / len(tests), 1) if tests else None
    best_test = max((t.score for t in tests), default=None)
    practice_correct = sum(p.practice_correct or 0 for p in progresses)
    practice_total = sum(p.practice_total or 0 for p in progresses)
    current = None
    for lsn in bank.all():
        st = states[lsn.number]
        if st["unlocked"] and not st["practice"]:
            current = (lsn, st)
            break
    return render_template(
        "student/dashboard.html",
        lessons=bank.all(),
        states=states,
        done=done,
        unlocked=unlocked,
        avg_test=avg_test,
        best_test=best_test,
        test_count=len(tests),
        practice_correct=practice_correct,
        practice_total=practice_total,
        current=current,
        success=lesson_success_map(current_user.id),
    )


def _profile_page(email: str, level_band: str):
    return render_template("student/profile.html", email=email, level_band=level_band)


@bp.route("/cabinet/profile", methods=["GET", "POST"])
@student_required
def profile():
    if request.method == "GET":
        return _profile_page(current_user.email or "", current_user.level_band or "1-2")
    if request.form.get("action") == "password":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("new_password2") or ""
        error = None
        if not current_user.check_password(current):
            error = "Текущий пароль неверный."
        elif new != confirm:
            error = "Пароль и подтверждение не совпадают."
        else:
            error = password_error(
                new,
                username=current_user.username,
                email=current_user.email or "",
            )
        if error is None and current_user.check_password(new):
            error = "Новый пароль совпадает с текущим."
        if error:
            flash(error, "warning")
            return _profile_page(current_user.email or "", current_user.level_band or "1-2")
        current_user.set_password(new)
        log_activity(current_user.id, "password_changed", actor_id=current_user.id)
        db.session.commit()
        flash("Пароль изменён.", "success")
        return redirect(url_for("student.profile"))

    email = (request.form.get("email") or "").strip()
    level_band = request.form.get("level_band") or current_user.level_band
    error = email_error(email)
    if error is None and level_band not in LEVEL_BANDS:
        error = "Выберите уровень заданий."
    if error is None and User.email_taken(email, exclude_id=current_user.id):
        error = "Такая почта уже используется."
    if error:
        flash(error, "warning")
        return _profile_page(email, level_band if level_band in LEVEL_BANDS else current_user.level_band)
    old_level = current_user.level_band
    current_user.email = normalize_email(email)
    if level_band in LEVEL_BANDS:
        current_user.level_band = level_band
    if old_level != current_user.level_band:
        sync_assignments_after_level_change(current_user, current_user.level_band)
    log_activity(
        current_user.id,
        "profile_updated",
        actor_id=current_user.id,
        details={"old_level": old_level, "new_level": current_user.level_band},
    )
    db.session.commit()
    flash("Профиль сохранён.", "success")
    return redirect(url_for("student.profile"))


def _support_form(name: str, email: str, kind: str, body: str):
    sent = (
        SupportMessage.query.filter_by(user_id=current_user.id)
        .order_by(SupportMessage.created_at.desc())
        .limit(20)
        .all()
    )
    return render_template(
        "student/messages.html",
        name=name,
        email=email,
        kind=kind if kind in SUPPORT_KINDS else "question",
        body=body,
        kinds=SUPPORT_KINDS,
        sent=sent,
    )


@bp.route("/cabinet/messages", methods=["GET", "POST"])
@student_required
def messages():
    name = current_user.full_name
    email = current_user.email or ""
    kind = "question"
    body = ""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        kind = (request.form.get("kind") or "").strip()
        body = (request.form.get("body") or "").strip()
        error = None
        if len(name) < 2 or len(name) > 160:
            error = "Укажите имя — от 2 до 160 символов."
        elif email_error(email):
            error = email_error(email)
        elif kind not in SUPPORT_KINDS:
            error = "Выберите, это вопрос, жалоба или другое."
        elif len(body) < 10:
            error = "Опишите вопрос или проблему подробнее, хотя бы в паре предложений."
        elif len(body) > 4000:
            error = "Текст слишком длинный. Сократите его до 4000 символов."
        if error:
            flash(error, "warning")
            return _support_form(name, email, kind, body)
        db.session.add(
            SupportMessage(
                user_id=current_user.id,
                kind=kind,
                name=name,
                email=normalize_email(email),
                body=body,
            )
        )
        db.session.commit()
        flash("Сообщение отправлено. Администратор его увидит.", "success")
        return redirect(url_for("student.messages"))
    return _support_form(name, email, kind, body)


@bp.route("/lessons/<int:number>")
@student_required
def lesson(number: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    if test_done(st["progress"]):
        ensure_assignments(current_user, number)
        refresh_practice_progress(current_user, number)
        st = lesson_state(current_user, number)
    log_activity(current_user.id, "lesson_open", lesson_number=number)
    db.session.commit()
    return render_template(
        "student/lesson.html",
        lesson=lsn,
        state=st,
        has_video=find_lesson_video(number) is not None,
    )


@bp.route("/lessons/<int:number>/theory", methods=["GET", "POST"])
@student_required
def theory(number: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            pct = int(data.get("pct") or 0)
        except (TypeError, ValueError):
            pct = 0
        completed = bool(data.get("completed"))
        try:
            seconds = int(data.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        progress = mark_theory_progress(
            current_user, number, pct, completed, seconds=seconds
        )
        db.session.commit()
        return jsonify(
            {
                "ok": True,
                "pct": progress.theory_scroll_pct,
                "completed": bool(progress.theory_completed_at),
                "test_open": theory_done(progress),
            }
        )
    log_activity(current_user.id, "theory_open", lesson_number=number)
    db.session.commit()
    video_path = find_lesson_video(number)
    video_name = video_path.name if video_path else ""
    video_done = bool(st["progress"].theory_video_completed_at)
    return render_template(
        "student/theory.html",
        lesson=lsn,
        state=st,
        video_name=video_name,
        video_done=video_done,
    )


@bp.route("/lessons/<int:number>/theory/video", methods=["POST"])
@student_required
def theory_video(number: int):
    if not is_unlocked(current_user, number):
        abort(403)
    if not find_lesson_video(number):
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        watched = float(data.get("watched") or 0)
    except (TypeError, ValueError):
        watched = 0.0
    try:
        duration = float(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    progress = mark_theory_video(
        current_user, number, watched=watched, duration=duration
    )
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "completed": bool(progress.theory_video_completed_at),
            "test_open": theory_done(progress),
        }
    )


def _chat_rows(number: int):
    return (
        TheoryChatMessage.query.filter_by(user_id=current_user.id, lesson_number=number)
        .order_by(TheoryChatMessage.created_at.asc(), TheoryChatMessage.id.asc())
        .all()
    )


def _ready_chat_history(number: int) -> list[dict[str, str]]:
    return [
        {"role": row.role, "content": row.content}
        for row in _chat_rows(number)
        if (row.status or "ready") == "ready" and (row.content or "").strip()
    ]


def _tutor_quote(number: int, question: str):
    lesson = _lesson(number)
    messages = build_tutor_messages(
        title=lesson.title,
        number=lesson.number,
        theory_md=lesson.theory_body,
        history=_ready_chat_history(number),
        question=question,
    )
    return quote_tokens(estimate_messages_tokens(messages), tutor_completion_cap())


def _chat_payload(row: TheoryChatMessage) -> dict:
    pending = (row.status or "ready") == TUTOR_PENDING
    if pending:
        html = f"<p>{TUTOR_PLACEHOLDER}</p>"
    else:
        html = format_message_html(row.role, row.content)
    return {
        "id": row.id,
        "role": row.role,
        "html": html,
        "pending": pending,
    }


@bp.route("/lessons/<int:number>/theory/chat", methods=["GET", "POST"])
@student_required
def theory_chat(number: int):
    if not is_unlocked(current_user, number):
        abort(403)
    _lesson(number)
    sweep_stale_tutor()
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "messages": [_chat_payload(row) for row in _chat_rows(number)],
            "balance": format_byn(current_user.balance_byn),
        })
    if not ai_configured():
        return jsonify(
            {"ok": False, "error": "Помощник пока не настроен. Администратору нужно указать ключ API."}
        ), 503
    data = request.get_json(silent=True) or {}
    question = str(data.get("message") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "Напишите вопрос по материалу занятия."}), 400
    if len(question) > MAX_QUESTION:
        question = question[:MAX_QUESTION]
    existing = pending_tutor_message(current_user.id, number)
    if existing is not None:
        return jsonify(
            {
                "ok": False,
                "pending": True,
                "error": "Помощник ещё отвечает. Подождите немного.",
                "assistant_id": existing.id,
            }
        ), 409
    need = _tutor_quote(number, question)
    ok, balance = affordable(current_user.id, need)
    if not ok:
        return jsonify({
            "ok": False,
            "error": insufficient_message(),
            "balance": format_byn(balance),
        }), 402
    user_row = TheoryChatMessage(
        user_id=current_user.id,
        lesson_number=number,
        role="user",
        content=question,
        status="ready",
    )
    bot_row = TheoryChatMessage(
        user_id=current_user.id,
        lesson_number=number,
        role="assistant",
        content=TUTOR_PLACEHOLDER,
        status=TUTOR_PENDING,
    )
    db.session.add(user_row)
    db.session.add(bot_row)
    db.session.commit()
    start_tutor_reply(current_app._get_current_object(), bot_row.id)
    return jsonify(
        {
            "ok": True,
            "pending": True,
            "user": _chat_payload(user_row),
            "assistant": _chat_payload(bot_row),
        }
    )


@bp.route("/lessons/<int:number>/theory/chat/clear", methods=["POST"])
@student_required
def theory_chat_clear(number: int):
    if not is_unlocked(current_user, number):
        abort(403)
    TheoryChatMessage.query.filter_by(user_id=current_user.id, lesson_number=number).delete()
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/lessons/<int:number>/test", methods=["GET", "POST"])
@student_required
def test(number: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    if not theory_done(st["progress"]):
        if find_lesson_video(number):
            flash(
                "Сначала пройдите теорию: досмотрите видео на 90% без перемотки вперёд или дочитайте текст до конца.",
                "info",
            )
        else:
            flash("Сначала дочитайте теорию до конца — тогда откроется тест.", "info")
        return redirect(url_for("student.theory", number=number))
    if request.method == "POST":
        questions_src = lsn.test_for(current_user.level_band)
        answers: dict[str, list[str]] = {}
        for q in questions_src:
            answers[q.code] = request.form.getlist(q.code)
        attempt = record_test_attempt(current_user, number, answers)
        db.session.commit()
        return redirect(url_for("student.test_result", number=number, attempt_id=attempt.id))
    attempts = (
        TestAttempt.query.filter_by(user_id=current_user.id, lesson_number=number)
        .order_by(TestAttempt.created_at.desc())
        .all()
    )
    log_activity(current_user.id, "test_start", lesson_number=number)
    db.session.commit()
    questions = [
        {
            "q": q,
            "prompt_html": render_markdown(display_prompt(q.prompt_md, q.multi)),
            "options": [(letter, render_markdown(text)) for letter, text in q.options],
        }
        for q in lsn.test_for(current_user.level_band)
    ]
    return render_template(
        "student/test.html",
        lesson=lsn,
        state=st,
        questions=questions,
        attempts=attempts,
        pass_percent=current_app.config["TEST_PASS_PERCENT"],
    )


@bp.route("/lessons/<int:number>/test/<int:attempt_id>")
@student_required
def test_result(number: int, attempt_id: int):
    attempt = TestAttempt.query.filter_by(
        id=attempt_id, user_id=current_user.id, lesson_number=number
    ).first_or_404()
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    return render_template(
        "student/test_result.html",
        lesson=lsn,
        state=st,
        attempt=attempt,
        pass_percent=current_app.config["TEST_PASS_PERCENT"],
    )


@bp.route("/lessons/<int:number>/practice")
@student_required
def practice(number: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    if not test_done(st["progress"]):
        flash("Практическая часть откроется после теста на 80% и выше.", "info")
        return redirect(url_for("student.lesson", number=number))
    rows = ensure_assignments(current_user, number)
    db.session.commit()
    sweep_stale_pending()
    current_id = request.args.get("task", type=int)
    current = next((r for r in rows if r.id == current_id), None)
    if current is None:
        current = next((r for r in rows if not r.is_correct), None) or (rows[0] if rows else None)
    if current is None:
        flash("Для практической части пока нет заданий.", "warning")
        return redirect(url_for("student.lesson", number=number))
    pending_ids = pending_assignment_ids([r.id for r in rows])
    current_pending = current.id in pending_ids
    next_task = next(
        (r for r in rows if not r.is_correct and r.id != current.id),
        None,
    )
    attempts = (
        PracticeAttempt.query.filter_by(assignment_id=current.id)
        .filter(PracticeAttempt.ai_verdict != "error")
        .order_by(PracticeAttempt.created_at.desc())
        .all()
    )
    log_activity(current_user.id, "practice_open", lesson_number=number, details={"task": current.task_code})
    db.session.commit()
    return render_template(
        "student/practice.html",
        lesson=lsn,
        state=st,
        assignments=rows,
        current=current,
        attempts=attempts,
        pending_ids=pending_ids,
        current_pending=current_pending,
        next_task=next_task,
        pass_percent=current_app.config["PRACTICE_PASS_PERCENT"],
        ai_ready=ai_configured(),
        task_html=render_markdown(current.task_md),
        status_url=url_for("student.practice_status", number=number, task=current.id),
    )


@bp.route("/lessons/<int:number>/practice/<int:assignment_id>/submit", methods=["POST"])
@student_required
def practice_submit(number: int, assignment_id: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    st = lesson_state(current_user, number)
    if not test_done(st["progress"]):
        return redirect(url_for("student.lesson", number=number))
    assignment = next(
        (r for r in active_assignments(current_user.id, number) if r.id == assignment_id),
        None,
    )
    if assignment is None:
        flash("Задание не найдено.", "danger")
        return redirect(url_for("student.practice", number=number))
    if assignment.is_correct:
        flash("Это задание уже засчитано.", "info")
        return redirect(url_for("student.practice", number=number, task=assignment.id))
    if pending_assignment_ids([assignment.id]):
        flash("Это решение ещё проверяется. Пока можно решать другие задания.", "info")
        return redirect(url_for("student.practice", number=number, task=assignment.id))
    if request.form.get("no_personal_data") != "1":
        flash("Перед отправкой подтвердите, что на фотографии нет имени, фамилии, лица и других личных данных.", "warning")
        return redirect(url_for("student.practice", number=number, task=assignment.id))
    file = request.files.get("photo")
    if not file or not file.filename:
        flash("Прикрепите фотографию решения.", "warning")
        return redirect(url_for("student.practice", number=number, task=assignment.id))
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        flash("Нужен файл JPG, PNG или WEBP.", "warning")
        return redirect(url_for("student.practice", number=number, task=assignment.id))
    prev = (
        PracticeAttempt.query.filter_by(assignment_id=assignment.id)
        .filter(PracticeAttempt.ai_verdict != "error")
        .order_by(PracticeAttempt.attempt_no.desc())
        .first()
    )
    attempt_no = (prev.attempt_no + 1) if prev else 1
    folder = UPLOADS_DIR / "practice" / str(current_user.id) / str(assignment.id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    raw_name = secure_filename(f"{stamp}_{attempt_no}_orig{ext}") or f"{stamp}_orig.jpg"
    proc_name = f"{stamp}_{attempt_no}_check.jpg"
    raw_path = folder / raw_name
    file.save(raw_path)
    proc_path = folder / proc_name
    try:
        prepare_photo(raw_path, proc_path)
    except Exception:
        proc_path = raw_path
        proc_name = raw_name
    rel_raw = str(Path("practice") / str(current_user.id) / str(assignment.id) / raw_name).replace("\\", "/")
    rel_proc = str(Path("practice") / str(current_user.id) / str(assignment.id) / proc_name).replace("\\", "/")
    previous_feedback = [
        a.ai_feedback
        for a in PracticeAttempt.query.filter_by(assignment_id=assignment.id)
        .order_by(PracticeAttempt.attempt_no.asc())
        .all()
        if (a.ai_feedback or "").strip() and a.ai_verdict not in {"unclear", "error", PENDING, "running"}
    ]
    if ai_configured():
        can_pay, _balance = affordable(current_user.id, Decimal("0.0001"))
        if not can_pay:
            for path in (raw_path, proc_path):
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            flash(insufficient_message(), "warning")
            return redirect(url_for("student.practice", number=number, task=assignment.id))
    attempt = PracticeAttempt(
        assignment_id=assignment.id,
        user_id=current_user.id,
        attempt_no=attempt_no,
        photo_filename=rel_raw,
        processed_filename=rel_proc,
        is_correct=False,
        ai_verdict=PENDING,
        ai_feedback="Проверяем решение…",
        ai_raw={"previous_feedback": previous_feedback},
    )
    db.session.add(attempt)
    log_activity(
        current_user.id,
        "practice_queued",
        lesson_number=number,
        details={"task": assignment.task_code, "attempt": attempt_no},
    )
    db.session.commit()
    start_practice_check(current_app._get_current_object(), attempt.id)
    flash("Решение отправлено. Пока оно проверяется, можно решать другие задачи.", "info")
    return redirect(url_for("student.practice", number=number, task=assignment.id))


@bp.route("/lessons/<int:number>/practice/status")
@student_required
def practice_status(number: int):
    if not is_unlocked(current_user, number):
        abort(403)
    rows = active_assignments(current_user.id, number)
    ids = [r.id for r in rows]
    pending = pending_assignment_ids(ids)
    current_id = request.args.get("task", type=int)
    current_pending = bool(current_id and current_id in pending)
    current_correct = False
    current_verdict = ""
    if current_id:
        assignment = next((r for r in rows if r.id == current_id), None)
        current_correct = bool(assignment and assignment.is_correct)
        latest = (
            PracticeAttempt.query.filter_by(assignment_id=current_id)
            .filter(PracticeAttempt.ai_verdict != "error")
            .order_by(PracticeAttempt.created_at.desc())
            .first()
        )
        if latest:
            current_verdict = latest.ai_verdict
    return jsonify(
        {
            "pending_ids": sorted(pending),
            "correct_ids": [r.id for r in rows if r.is_correct],
            "current_pending": current_pending,
            "current_correct": current_correct,
            "current_verdict": current_verdict,
        }
    )
