"""Фоновая подготовка работы над ошибками."""

from __future__ import annotations

import logging

from flask import Flask
from sqlalchemy import event
from sqlalchemy.orm import Session

from .ai_checker import ai_configured
from .ai_jobs import is_queued, submit_job
from .billing import (
    format_byn,
    hold,
    quote_tokens,
    release,
    settle,
    usage_byn,
    estimate_messages_tokens,
)
from .config import LEVEL_LABELS
from .content import get_bank
from .models import BalanceLedger, ErrorWork, User, db, utcnow

log = logging.getLogger(__name__)

_HOOKS = False


def is_error_work_queued(work_id: int) -> bool:
    return is_queued("error_work", work_id)


def arm_error_work(work_id: int) -> None:
    """Запуск после commit, чтобы фоновый поток увидел строку."""
    _install_hooks()
    pending: set[int] = db.session.info.setdefault("_ew_pending", set())
    pending.add(int(work_id))


def start_error_work(app: Flask, work_id: int) -> None:
    if is_queued("error_work", work_id):
        return
    submit_job("error_work", work_id, _run_error_work, app, work_id)


def resume_generating_error_work(app: Flask) -> None:
    with app.app_context():
        rows = ErrorWork.query.filter_by(status="generating").order_by(ErrorWork.id.asc()).all()
        ids = [row.id for row in rows]
    for work_id in ids:
        start_error_work(app, work_id)


def _install_hooks() -> None:
    global _HOOKS
    if _HOOKS:
        return
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _HOOKS = True


def _after_commit(session) -> None:
    ids = [int(item) for item in session.info.pop("_ew_pending", ())]
    if not ids:
        return
    try:
        from flask import current_app

        app = current_app._get_current_object()
    except RuntimeError:
        log.warning("error work queued without app context: %s", ids)
        return
    for work_id in ids:
        start_error_work(app, work_id)


def _after_rollback(session) -> None:
    session.info.pop("_ew_pending", None)


def _run_error_work(app: Flask, work_id: int) -> None:
    with app.app_context():
        try:
            _generate_and_store(app, work_id)
        except Exception:
            app.logger.exception("error work job crashed")
            _mark_failed(work_id, "Не удалось подготовить задания. Деньги не списаны. Попробуйте ещё раз.", None, None)
        finally:
            db.session.remove()


def _release_stale_holds(work_id: int) -> None:
    rows = (
        BalanceLedger.query.filter(
            BalanceLedger.ref.like(f"error_work:{work_id}:%"),
            BalanceLedger.status == "held",
        )
        .order_by(BalanceLedger.id.asc())
        .all()
    )
    if not rows:
        return
    for row in rows:
        release(row.id, commit=False)
    db.session.commit()


def _generate_and_store(app: Flask, work_id: int) -> None:
    from .error_work import (
        GenerationFailed,
        build_messages,
        error_work_completion_cap,
        generate_error_work,
    )

    work = (
        db.session.query(ErrorWork)
        .filter_by(id=work_id, status="generating")
        .with_for_update()
        .one_or_none()
    )
    if work is None:
        db.session.rollback()
        return
    if work.questions:
        work.status = "ready"
        if work.ready_at is None:
            work.ready_at = utcnow()
        db.session.commit()
        return
    user_id = work.user_id
    lesson_number = work.lesson_number
    question_count = int(work.question_count or 0)
    errors = list(work.errors_json or [])
    db.session.commit()

    if question_count <= 0 or not errors:
        _mark_failed(work_id, "Не удалось собрать ошибки занятия.", None, None)
        return
    _release_stale_holds(work_id)
    if not ai_configured():
        _mark_failed(work_id, "Подготовка заданий сейчас недоступна. Попробуйте позже.", None, None)
        return

    user = db.session.get(User, user_id)
    lesson = get_bank().get(lesson_number)
    if user is None:
        _mark_failed(work_id, "Не удалось подготовить задания.", None, None)
        return
    level = LEVEL_LABELS.get(user.level_band or "", user.level_band or "")
    messages = build_messages(
        lesson_number=lesson.number,
        title=lesson.title,
        level_label=level,
        goals=list(lesson.goals or []),
        errors=errors,
        question_count=question_count,
    )
    need = quote_tokens(estimate_messages_tokens(messages), error_work_completion_cap())
    work = db.session.get(ErrorWork, work_id)
    if work is None or work.status != "generating":
        return
    generation_no = int(work.generation_no or 0) + 1
    ref = f"error_work:{work_id}:{generation_no}"
    work.generation_no = generation_no
    hold_id = hold(user_id, need, "error_work", ref)
    if hold_id is None:
        work = db.session.get(ErrorWork, work_id)
        if work is not None and work.status == "generating":
            work.status = "needs_payment"
            work.fail_note = ""
            work.updated_at = utcnow()
            db.session.commit()
        return

    try:
        prepared = generate_error_work(
            lesson_number=lesson.number,
            title=lesson.title,
            level_label=level,
            goals=list(lesson.goals or []),
            errors=errors,
            question_count=question_count,
            model=app.config["AI_MODEL"],
        )
    except GenerationFailed as exc:
        note = (
            "Подготовка не удалась, запрос уже списан с баланса. Можно повторить."
            if exc.usage.calls
            else "Не удалось подготовить задания. Деньги не списаны. Попробуйте ещё раз."
        )
        _mark_failed(work_id, note, hold_id, exc.usage)
        return
    except Exception:
        app.logger.exception("error work generation crashed")
        _mark_failed(
            work_id,
            "Не удалось подготовить задания. Деньги не списаны. Попробуйте ещё раз.",
            hold_id,
            None,
        )
        return

    work = db.session.get(ErrorWork, work_id)
    if work is None or work.status != "generating":
        if prepared.usage.calls:
            settle(hold_id, usage_byn(prepared.usage), _usage_details(prepared.usage))
        else:
            release(hold_id)
        return
    work.report = prepared.report
    work.questions = prepared.questions
    work.question_count = len(prepared.questions)
    work.status = "ready"
    work.fail_note = ""
    work.ready_at = utcnow()
    work.updated_at = utcnow()
    charged = settle(hold_id, usage_byn(prepared.usage), _usage_details(prepared.usage), commit=False)
    from .progress import log_activity

    log_activity(
        user_id,
        "error_work_ready",
        lesson_number=lesson_number,
        details={"questions": len(prepared.questions), "charged": str(charged)},
    )
    db.session.commit()
    app.logger.info("error work ready user=%s lesson=%s byn=%s", user_id, lesson_number, format_byn(charged))


def _usage_details(usage) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "calls": usage.calls,
        "usd": str(usage.billed_usd()) if usage.calls else "0",
    }


def _mark_failed(work_id: int, note: str, hold_id: int | None, usage) -> None:
    db.session.rollback()
    work = db.session.get(ErrorWork, work_id)
    if work is not None and work.status == "generating":
        work.status = "failed"
        work.fail_note = note
        work.updated_at = utcnow()
    if hold_id is None:
        db.session.commit()
        return
    if usage is not None and getattr(usage, "calls", 0):
        settle(hold_id, usage_byn(usage), _usage_details(usage), commit=False)
    else:
        release(hold_id, commit=False)
    db.session.commit()
