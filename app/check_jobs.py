"""Фоновая проверка практики, чтобы ученик мог перейти к другой задаче."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread

from flask import Flask

from .ai_checker import (
    ai_configured,
    check_solution,
)
from .ai_jobs import is_queued, submit_job
from .billing import charge_for_response, format_byn, money, release_ref, usage_byn
from .config import UPLOADS_DIR
from .models import PracticeAssignment, PracticeAttempt, User, db, utcnow
from .progress import log_activity, refresh_practice_progress

PENDING = "pending"
RUNNING = "running"
CLAIM_STALE = timedelta(minutes=30)
_watch_lock = Lock()
_watch_started = False


def _unlink(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def _attempt_files(attempt: PracticeAttempt) -> list[Path]:
    names = [attempt.photo_filename, attempt.processed_filename]
    return [UPLOADS_DIR / name for name in names if name]


def _delete_attempt(attempt: PracticeAttempt) -> None:
    for path in _attempt_files(attempt):
        _unlink(path)
    db.session.delete(attempt)


def _claimed_recent(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    text = raw.get("claimed_at")
    if not text:
        return False
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return utcnow() - moment < CLAIM_STALE


def _revert_pending(attempt_id: int) -> None:
    attempt = db.session.get(PracticeAttempt, attempt_id)
    if attempt is not None and attempt.ai_verdict == RUNNING:
        attempt.ai_verdict = PENDING
        db.session.commit()


def resume_pending_practice(app: Flask, *, restart: bool = False) -> None:
    """Снова ставит в очередь фото, которые так и не получили вердикт."""
    with app.app_context():
        rows = (
            PracticeAttempt.query.filter(PracticeAttempt.ai_verdict.in_((PENDING, RUNNING)))
            .order_by(PracticeAttempt.id.asc())
            .all()
        )
        ids: list[int] = []
        for row in rows:
            if is_queued("practice", row.id):
                continue
            if row.ai_verdict == RUNNING and not restart and _claimed_recent(row.ai_raw):
                continue
            if row.ai_verdict == RUNNING:
                row.ai_verdict = PENDING
            ids.append(row.id)
        if ids:
            db.session.commit()
        else:
            db.session.rollback()
    for attempt_id in ids:
        start_practice_check(app, attempt_id)


def sweep_stale_pending() -> None:
    """Открытая страница практики тоже поднимает зависшие проверки, не удаляя их."""
    from flask import current_app

    resume_pending_practice(current_app._get_current_object(), restart=False)


def pending_assignment_ids(assignment_ids: list[int]) -> set[int]:
    if not assignment_ids:
        return set()
    rows = PracticeAttempt.query.filter(
        PracticeAttempt.assignment_id.in_(assignment_ids),
        PracticeAttempt.ai_verdict.in_((PENDING, RUNNING)),
    ).all()
    return {row.assignment_id for row in rows}


def start_practice_check(app: Flask, attempt_id: int) -> None:
    if is_queued("practice", attempt_id):
        return
    submit_job("practice", attempt_id, _run_practice_check, app, attempt_id)


def start_practice_watch(app: Flask) -> None:
    """Сразу после старта и затем раз в несколько часов допроверяет фото."""
    global _watch_started
    with _watch_lock:
        if _watch_started:
            return
        _watch_started = True
    Thread(target=_watch_loop, args=(app,), name="practice-watch", daemon=True).start()


def _watch_loop(app: Flask) -> None:
    restart = True
    while True:
        try:
            resume_pending_practice(app, restart=restart)
        except Exception:
            app.logger.exception("practice watch failed")
        restart = False
        hours = int(app.config.get("PRACTICE_RECHECK_HOURS") or 3)
        time.sleep(max(1, hours) * 3600)


def _run_practice_check(app: Flask, attempt_id: int) -> None:
    with app.app_context():
        try:
            _check_and_store(app, attempt_id)
        except Exception:
            app.logger.exception("background practice check crashed")
            _revert_pending(attempt_id)
        finally:
            db.session.remove()


def _check_and_store(app: Flask, attempt_id: int) -> None:
    attempt = (
        db.session.query(PracticeAttempt)
        .filter_by(id=attempt_id, ai_verdict=PENDING)
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        db.session.rollback()
        return
    assignment = db.session.get(PracticeAssignment, attempt.assignment_id)
    if assignment is None:
        _delete_attempt(attempt)
        db.session.commit()
        return
    user_id = attempt.user_id
    ref = f"practice:{attempt.id}"
    release_ref(ref, commit=False)
    user = db.session.get(User, user_id)
    if user is None or money(user.balance_byn) <= 0:
        db.session.commit()
        return
    rel = attempt.processed_filename or attempt.photo_filename
    image = UPLOADS_DIR / rel
    if not image.is_file():
        image = UPLOADS_DIR / attempt.photo_filename
    stored = (attempt.ai_raw or {}).get("previous_feedback") if isinstance(attempt.ai_raw, dict) else None
    previous_feedback = stored if isinstance(stored, list) else [
        a.ai_feedback
        for a in PracticeAttempt.query.filter_by(assignment_id=assignment.id)
        .order_by(PracticeAttempt.attempt_no.asc())
        .all()
        if a.id != attempt.id
        and (a.ai_feedback or "").strip()
        and a.ai_verdict not in {"unclear", "error", PENDING, RUNNING}
    ]
    task_md = f"{assignment.title}\n\n{assignment.task_md}"
    attempt_no = attempt.attempt_no
    raw = dict(attempt.ai_raw or {})
    raw["claimed_at"] = utcnow().isoformat()
    attempt.ai_raw = raw
    attempt.ai_verdict = RUNNING
    db.session.commit()

    if not image.is_file() or not ai_configured():
        _revert_pending(attempt_id)
        return
    try:
        result = check_solution(
            task_md=task_md,
            image_path=image,
            model=app.config["AI_MODEL"],
            attempt_no=attempt_no,
            previous_feedback=previous_feedback,
        )
    except Exception:
        app.logger.exception("background practice check failed")
        _revert_pending(attempt_id)
        return

    verdict = str(result.get("verdict") or "").lower()
    if verdict in {"", "error"}:
        _revert_pending(attempt_id)
        return

    attempt = db.session.get(PracticeAttempt, attempt_id)
    if attempt is None or attempt.ai_verdict != RUNNING:
        return
    assignment = db.session.get(PracticeAssignment, attempt.assignment_id)
    usage = result.get("usage")
    if usage is not None and getattr(usage, "calls", 0):
        charged = charge_for_response(
            user_id,
            usage_byn(usage),
            "practice",
            ref,
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "calls": usage.calls,
                "usd": str(usage.billed_usd()),
            },
            commit=False,
        )
        app.logger.info("practice charge user=%s byn=%s", user_id, format_byn(charged))
    attempt.is_correct = bool(result.get("is_correct"))
    if verdict == "correct":
        attempt.is_correct = True
    attempt.ai_verdict = verdict or ("correct" if attempt.is_correct else "incorrect")
    attempt.ai_feedback = str(result.get("feedback") or "").strip()
    attempt.ai_raw = result.get("raw")
    if attempt.is_correct and assignment is not None:
        assignment.is_correct = True
    if assignment is not None:
        user = db.session.get(User, attempt.user_id)
        if user is not None:
            refresh_practice_progress(user, assignment.lesson_number)
        log_activity(
            attempt.user_id,
            "practice_submit",
            lesson_number=assignment.lesson_number,
            details={
                "task": assignment.task_code,
                "attempt": attempt.attempt_no,
                "correct": attempt.is_correct,
                "verdict": attempt.ai_verdict,
            },
        )
    db.session.commit()
