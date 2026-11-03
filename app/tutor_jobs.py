"""Фоновые ответы тьютора: HTTP сразу возвращается, модель работает в очереди."""

from __future__ import annotations

from datetime import timedelta

from flask import Flask

from .ai_checker import ai_configured
from .ai_jobs import is_queued, submit_job
from .billing import (
    ModelUsage,
    estimate_messages_tokens,
    format_byn,
    hold,
    insufficient_message,
    quote_tokens,
    release,
    release_ref,
    settle,
    usage_byn,
)
from .content import get_bank
from .models import TheoryChatMessage, db, utcnow
from .theory_tutor import ask_tutor, build_tutor_messages, tutor_completion_cap

PENDING = "pending"
READY = "ready"
ERROR = "error"
STALE_AFTER = timedelta(minutes=20)
ERROR_TEXT = "Не удалось получить ответ. Попробуйте ещё раз чуть позже."
PLACEHOLDER = "Думаю…"


def sweep_stale_tutor() -> None:
    cutoff = utcnow() - STALE_AFTER
    rows = TheoryChatMessage.query.filter(
        TheoryChatMessage.role == "assistant",
        TheoryChatMessage.status == PENDING,
        TheoryChatMessage.created_at < cutoff,
    ).all()
    if not rows:
        return
    for row in rows:
        if is_queued("tutor", row.id):
            continue
        release_ref(f"tutor:{row.id}")
        row.status = ERROR
        row.content = ERROR_TEXT
    db.session.commit()


def pending_tutor_message(user_id: int, lesson_number: int) -> TheoryChatMessage | None:
    return (
        TheoryChatMessage.query.filter_by(
            user_id=user_id,
            lesson_number=lesson_number,
            role="assistant",
            status=PENDING,
        )
        .order_by(TheoryChatMessage.id.desc())
        .first()
    )


def start_tutor_reply(app: Flask, message_id: int) -> None:
    submit_job("tutor", message_id, _run_tutor_reply, app, message_id)


def resume_pending_tutor(app: Flask) -> None:
    sweep_stale_tutor()
    rows = (
        TheoryChatMessage.query.filter_by(role="assistant", status=PENDING)
        .order_by(TheoryChatMessage.id.asc())
        .all()
    )
    for row in rows:
        start_tutor_reply(app, row.id)


def _run_tutor_reply(app: Flask, message_id: int) -> None:
    with app.app_context():
        try:
            _answer_and_store(app, message_id)
        except Exception:
            app.logger.exception("background tutor reply crashed")
            release_ref(f"tutor:{message_id}")
            row = db.session.get(TheoryChatMessage, message_id)
            if row is not None and row.status == PENDING:
                row.status = ERROR
                row.content = ERROR_TEXT
                db.session.commit()
        finally:
            db.session.remove()


def _charge_details(usage: ModelUsage) -> dict:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "calls": usage.calls,
        "usd": str(usage.billed_usd()) if usage.calls else "0",
    }


def _answer_and_store(app: Flask, message_id: int) -> None:
    row = db.session.get(TheoryChatMessage, message_id)
    if row is None or row.status != PENDING or row.role != "assistant":
        return
    question_row = (
        TheoryChatMessage.query.filter(
            TheoryChatMessage.user_id == row.user_id,
            TheoryChatMessage.lesson_number == row.lesson_number,
            TheoryChatMessage.role == "user",
            TheoryChatMessage.id < row.id,
        )
        .order_by(TheoryChatMessage.id.desc())
        .first()
    )
    if question_row is None or not (question_row.content or "").strip():
        row.status = ERROR
        row.content = "Напишите вопрос по материалу занятия."
        db.session.commit()
        return
    skip_ids = {row.id, question_row.id}
    history = [
        {"role": item.role, "content": item.content}
        for item in TheoryChatMessage.query.filter_by(
            user_id=row.user_id,
            lesson_number=row.lesson_number,
        )
        .order_by(TheoryChatMessage.created_at.asc(), TheoryChatMessage.id.asc())
        .all()
        if item.id not in skip_ids and (item.status or READY) not in {PENDING, ERROR}
    ]
    user_id = row.user_id
    lesson_number = row.lesson_number
    question = question_row.content
    ref = f"tutor:{message_id}"
    hold_id = None
    try:
        lesson = get_bank().get(lesson_number)
        messages = build_tutor_messages(
            title=lesson.title,
            number=lesson.number,
            theory_md=lesson.theory_body,
            history=history,
            question=question,
        )
        if ai_configured():
            need = quote_tokens(estimate_messages_tokens(messages), tutor_completion_cap())
            hold_id = hold(user_id, need, "tutor", ref)
            if hold_id is None:
                row = db.session.get(TheoryChatMessage, message_id)
                if row is not None and row.status == PENDING:
                    row.status = ERROR
                    row.content = insufficient_message()
                    db.session.commit()
                return
        answer, usage = ask_tutor(
            title=lesson.title,
            number=lesson.number,
            theory_md=lesson.theory_body,
            history=history,
            question=question,
            model=app.config["AI_MODEL"],
        )
    except Exception:
        app.logger.exception("background tutor reply failed")
        answer = ERROR_TEXT
        usage = ModelUsage()

    row = db.session.get(TheoryChatMessage, message_id)
    if row is None or row.status != PENDING:
        if hold_id is not None and usage.calls:
            settle(hold_id, usage_byn(usage), _charge_details(usage))
        elif hold_id is not None:
            release(hold_id)
        return
    text = (answer or "").strip() or ERROR_TEXT
    row.content = text
    row.status = READY
    if hold_id is not None and usage.calls:
        charged = settle(hold_id, usage_byn(usage), _charge_details(usage))
        app.logger.info("tutor charge user=%s byn=%s", user_id, format_byn(charged))
    elif hold_id is not None:
        release(hold_id)
    else:
        db.session.commit()
