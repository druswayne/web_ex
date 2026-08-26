from __future__ import annotations

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

from ..ai_checker import ai_configured, prepare_photo
from ..check_jobs import (
    PENDING,
    pending_assignment_ids,
    start_practice_check,
    sweep_stale_pending,
)
from ..config import LEVEL_LABELS, UPLOADS_DIR
from ..content import display_prompt, get_bank, render_markdown
from ..models import (
    PracticeAttempt,
    TestAttempt,
    UserAchievement,
    db,
    utcnow,
)
from ..progress import (
    ACHIEVEMENTS,
    active_assignments,
    ensure_assignments,
    is_unlocked,
    lesson_state,
    log_activity,
    mark_theory_progress,
    record_test_attempt,
    refresh_practice_progress,
    test_done,
    theory_done,
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
    earned = UserAchievement.query.filter_by(user_id=current_user.id).order_by(
        UserAchievement.earned_at.desc()
    ).all()
    earned_codes = {a.code for a in earned}
    catalog = [
        {
            "code": code,
            "title": title,
            "description": desc,
            "earned": next((a for a in earned if a.code == code), None),
        }
        for code, title, desc in ACHIEVEMENTS
    ]
    current = None
    for lsn in bank.all():
        st = states[lsn.number]
        if st["unlocked"] and not st["practice"]:
            current = (lsn, st)
            break
    recent_tests = (
        TestAttempt.query.filter_by(user_id=current_user.id)
        .order_by(TestAttempt.created_at.desc())
        .limit(5)
        .all()
    )
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
        achievements=catalog,
        earned_n=len(earned_codes),
        current=current,
        recent_tests=recent_tests,
        level_label=LEVEL_LABELS.get(current_user.level_band or "", current_user.level_band),
    )


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
    return render_template("student/lesson.html", lesson=lsn, state=st)


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
        pct = int(data.get("pct") or 0)
        completed = bool(data.get("completed"))
        progress = mark_theory_progress(current_user, number, pct, completed)
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
    return render_template("student/theory.html", lesson=lsn, state=st)


@bp.route("/lessons/<int:number>/test", methods=["GET", "POST"])
@student_required
def test(number: int):
    if not is_unlocked(current_user, number):
        flash("Это занятие ещё закрыто.", "warning")
        return redirect(url_for("student.dashboard"))
    lsn = _lesson(number)
    st = lesson_state(current_user, number)
    if not theory_done(st["progress"]):
        flash("Сначала дочитайте теорию до конца — тогда откроется тест.", "info")
        return redirect(url_for("student.theory", number=number))
    if request.method == "POST":
        answers: dict[str, list[str]] = {}
        for q in lsn.test:
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
        for q in lsn.test
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
        flash("Для вашего уровня пока нет заданий практической части.", "warning")
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
        task_html=render_markdown(f"**{current.title}**\n\n{current.task_md}"),
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
        flash("Это решение ещё проверяется. Можно пока открыть другое задание.", "info")
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
        if (a.ai_feedback or "").strip() and a.ai_verdict not in {"unclear", "error", PENDING}
    ]
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
    flash("Решение отправлено на проверку. Можно перейти к другой задаче — ждать здесь не обязательно.", "info")
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
