"""Снимок ошибок занятия и подготовка теста для работы над ними."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from flask import current_app

from .ai_checker import _client, _request_model_text, ai_configured
from .billing import (
    ModelUsage,
    absorb_usage,
    affordable,
    estimate_messages_tokens,
    quote_tokens,
)
from .config import LEVEL_LABELS
from .content import get_bank
from .models import (
    ErrorWork,
    ErrorWorkAttempt,
    LessonProgress,
    PracticeAssignment,
    PracticeAttempt,
    TestAttempt,
    User,
    db,
    utcnow,
)
from .progress import log_activity, mark_lesson_finished, meets_percent

log = logging.getLogger(__name__)

MIN_QUESTIONS = 10
MAX_QUESTIONS = 20
_SKIP_VERDICTS = {"pending", "running", "error", "unclear", ""}
_CYR = ("а", "б", "в", "г")
_LETTER = {
    "a": "а",
    "b": "б",
    "c": "в",
    "d": "г",
    "а": "а",
    "б": "б",
    "в": "в",
    "г": "г",
    "1": "а",
    "2": "б",
    "3": "в",
    "4": "г",
}
_TASK_LIMIT = 1800
_NOTE_LIMIT = 500


class GenerationFailed(Exception):
    def __init__(self, usage: ModelUsage):
        super().__init__("generation failed")
        self.usage = usage


@dataclass
class PreparedWork:
    report: dict[str, Any]
    questions: list[dict[str, Any]]
    usage: ModelUsage


def question_target(error_count: int) -> int:
    if error_count <= 0:
        return 0
    return min(MAX_QUESTIONS, max(MIN_QUESTIONS, error_count))


def error_work_completion_cap() -> int:
    raw = (os.getenv("AI_ERROR_WORK_MAX_TOKENS") or "8192").strip()
    try:
        return max(1024, int(raw))
    except ValueError:
        return 8192


def get_error_work(user_id: int, lesson_number: int) -> ErrorWork | None:
    return ErrorWork.query.filter_by(user_id=user_id, lesson_number=lesson_number).first()


def topic_labels(errors: list[dict[str, Any]] | None) -> list[str]:
    labels: list[str] = []
    for item in errors or []:
        name = str(item.get("topic") or item.get("title") or item.get("code") or "").strip()
        kind = "Тест" if item.get("source") == "test" else "Практика"
        label = f"{kind} · {name}" if name else kind
        if label not in labels:
            labels.append(label)
    return labels


def _clip(text: str, limit: int) -> str:
    body = re.sub(r"\s+", " ", (text or "").strip())
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


def _wrong_test_items(user_id: int, lesson_number: int, level_band: str | None) -> list[dict[str, Any]]:
    lesson = get_bank().get(lesson_number)
    by_code: dict[str, Any] = {}
    for questions in lesson.tests.values():
        for question in questions:
            by_code.setdefault(question.code, question)
    for question in lesson.test_for(level_band):
        by_code[question.code] = question

    grouped: dict[str, dict[str, Any]] = {}
    attempts = (
        TestAttempt.query.filter_by(user_id=user_id, lesson_number=lesson_number)
        .order_by(TestAttempt.attempt_no.asc())
        .all()
    )
    for attempt in attempts:
        details = attempt.details if isinstance(attempt.details, list) else []
        for item in details:
            if not isinstance(item, dict) or item.get("correct"):
                continue
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            bucket = grouped.get(code)
            if bucket is None:
                question = by_code.get(code)
                bucket = {
                    "source": "test",
                    "code": code,
                    "topic": str(item.get("topic") or getattr(question, "topic", "") or "").strip(),
                    "title": "",
                    "prompt": getattr(question, "prompt_md", "") or "",
                    "options": [
                        {"letter": letter, "text": text}
                        for letter, text in (getattr(question, "options", None) or [])
                    ],
                    "correct": list(getattr(question, "answers", None) or item.get("expected") or []),
                    "picks": [],
                    "wrong_count": 0,
                    "later_correct": False,
                }
                grouped[code] = bucket
            selected = list(item.get("selected") or [])
            if selected not in bucket["picks"] and len(bucket["picks"]) < 6:
                bucket["picks"].append(selected)
            bucket["wrong_count"] += 1
            if not bucket["topic"]:
                bucket["topic"] = str(item.get("topic") or "").strip()
            if not bucket["correct"] and item.get("expected"):
                bucket["correct"] = list(item.get("expected") or [])
        for item in details:
            if not isinstance(item, dict) or not item.get("correct"):
                continue
            code = str(item.get("code") or "").strip()
            if code in grouped:
                grouped[code]["later_correct"] = True
    return list(grouped.values())


def _practice_notes(attempt: PracticeAttempt) -> dict[str, str]:
    raw = attempt.ai_raw if isinstance(attempt.ai_raw, dict) else {}
    return {
        "attempt": str(attempt.attempt_no),
        "feedback": _clip(attempt.ai_feedback or "", _NOTE_LIMIT),
        "error_kind": _clip(str(raw.get("error_kind") or ""), 40),
        "error_detail": _clip(str(raw.get("error_detail") or ""), _NOTE_LIMIT),
    }


def _wrong_practice_items(user_id: int, lesson_number: int) -> list[dict[str, Any]]:
    rows = (
        PracticeAssignment.query.filter_by(user_id=user_id, lesson_number=lesson_number)
        .order_by(PracticeAssignment.id.asc())
        .all()
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        bad = [
            attempt
            for attempt in row.attempts
            if (attempt.ai_verdict or "").lower() not in _SKIP_VERDICTS
            and not attempt.is_correct
            and (attempt.ai_verdict or "").lower() != "correct"
        ]
        if not bad:
            continue
        notes = [_practice_notes(attempt) for attempt in bad[-3:]]
        grouped[row.id] = {
            "source": "practice",
            "code": row.task_code,
            "topic": (row.title or row.task_code or "").strip(),
            "title": (row.title or "").strip(),
            "prompt": _clip(row.task_md or "", _TASK_LIMIT),
            "options": [],
            "correct": [],
            "picks": [],
            "notes": notes,
            "wrong_count": len(bad),
            "later_correct": bool(row.is_correct),
        }
    return list(grouped.values())


def collect_lesson_errors(user_id: int, lesson_number: int, level_band: str | None) -> list[dict[str, Any]]:
    items = _wrong_test_items(user_id, lesson_number, level_band)
    items.extend(_wrong_practice_items(user_id, lesson_number))
    items.sort(
        key=lambda item: (
            0 if item.get("source") == "test" else 1,
            -int(item.get("wrong_count") or 0),
            str(item.get("code") or ""),
        )
    )
    return items


def begin_error_work(user: User, lesson_number: int, progress: LessonProgress) -> ErrorWork:
    """Фиксирует ошибки в момент зачёта практики. Повторный вызов не пересобирает снимок."""
    existing = get_error_work(user.id, lesson_number)
    if existing is not None:
        if existing.required and progress.error_work_passed_at is None:
            queue_error_work(user, existing)
        elif not existing.required:
            progress.error_work_required = False
            mark_lesson_finished(user, progress)
        return existing

    errors = collect_lesson_errors(user.id, lesson_number, user.level_band)
    work = ErrorWork(
        user_id=user.id,
        lesson_number=lesson_number,
        required=bool(errors),
        status="not_required" if not errors else "needs_payment",
        error_count=len(errors),
        question_count=question_target(len(errors)),
        errors_json=errors,
        fail_note="",
    )
    db.session.add(work)
    db.session.flush()
    if not errors:
        progress.error_work_required = False
        mark_lesson_finished(user, progress)
        log_activity(
            user.id,
            "error_work_skipped",
            lesson_number=lesson_number,
            details={"reason": "no_errors"},
        )
        return work

    progress.error_work_required = True
    log_activity(
        user.id,
        "error_work_required",
        lesson_number=lesson_number,
        details={"errors": len(errors), "questions": work.question_count},
    )
    queue_error_work(user, work)
    return work


def queue_error_work(user: User, work: ErrorWork, *, allow_failed: bool = False) -> str:
    """Ставит подготовку в очередь, если на балансе хватает оценки запроса."""
    from .error_jobs import arm_error_work, is_error_work_queued

    db.session.refresh(work)
    if not work.required or work.status in {"ready", "not_required"}:
        return work.status
    if work.status == "generating" and is_error_work_queued(work.id):
        return "generating"
    if work.status == "failed" and not allow_failed:
        return "failed"
    if not ai_configured():
        work.status = "failed"
        work.fail_note = "Подготовка заданий сейчас недоступна. Попробуйте позже."
        work.updated_at = utcnow()
        return work.status

    try:
        need = preparation_quote(work, user.level_band)
    except Exception:
        log.exception("error work quote failed")
        work.status = "failed"
        work.fail_note = "Не удалось оценить подготовку. Попробуйте ещё раз."
        work.updated_at = utcnow()
        return work.status
    ok, _balance = affordable(user.id, need)
    if not ok:
        work.status = "needs_payment"
        work.fail_note = ""
        work.updated_at = utcnow()
        return work.status

    work.status = "generating"
    work.fail_note = ""
    work.updated_at = utcnow()
    arm_error_work(work.id)
    return work.status


def preparation_quote(work: ErrorWork, level_band: str | None) -> Any:
    lesson = get_bank().get(work.lesson_number)
    messages = build_messages(
        lesson_number=lesson.number,
        title=lesson.title,
        level_label=LEVEL_LABELS.get(level_band or "", level_band or ""),
        goals=list(lesson.goals or []),
        errors=list(work.errors_json or []),
        question_count=int(work.question_count or 0),
    )
    return quote_tokens(estimate_messages_tokens(messages), error_work_completion_cap())


def pass_percent() -> float:
    return float(current_app.config.get("ERROR_WORK_PASS_PERCENT") or 80)


def record_error_attempt(user: User, work: ErrorWork, answers: dict[str, list[str]]) -> ErrorWorkAttempt:
    questions = list(work.questions or [])
    details = []
    correct = 0
    for question in questions:
        code = str(question.get("code") or "")
        selected = sorted(answers.get(code) or [])
        expected = sorted(str(item) for item in (question.get("answers") or []))
        ok = bool(expected) and selected == expected
        if ok:
            correct += 1
        details.append(
            {
                "code": code,
                "topic": question.get("topic") or "",
                "selected": selected,
                "correct": ok,
                "multi": bool(question.get("multi")),
            }
        )
    total = len(questions) or 1
    score = round(100.0 * correct / total, 1)
    passed = meets_percent(correct, len(questions), pass_percent())
    prev = (
        ErrorWorkAttempt.query.filter_by(error_work_id=work.id)
        .order_by(ErrorWorkAttempt.attempt_no.desc())
        .first()
    )
    attempt = ErrorWorkAttempt(
        error_work_id=work.id,
        user_id=user.id,
        lesson_number=work.lesson_number,
        attempt_no=(prev.attempt_no + 1) if prev else 1,
        answers=answers,
        details=details,
        correct_count=correct,
        total_count=len(questions),
        score=score,
        passed=passed,
    )
    db.session.add(attempt)
    if work.best_score is None or score > work.best_score:
        work.best_score = score
    work.updated_at = utcnow()
    progress = LessonProgress.query.filter_by(
        user_id=user.id, lesson_number=work.lesson_number
    ).first()
    if passed and progress is not None and progress.error_work_passed_at is None:
        progress.error_work_passed_at = utcnow()
        mark_lesson_finished(user, progress)
    log_activity(
        user.id,
        "error_work_submit",
        lesson_number=work.lesson_number,
        details={"attempt": attempt.attempt_no, "score": score, "passed": passed},
    )
    return attempt


def build_messages(
    *,
    lesson_number: int,
    title: str,
    level_label: str,
    goals: list[str],
    errors: list[dict[str, Any]],
    question_count: int,
) -> list[dict[str, str]]:
    goal_lines = []
    for goal in goals[:8]:
        text = _clip(str(goal), 220)
        if text:
            goal_lines.append(f"- {text}")
    blocks = [_format_error(index, item) for index, item in enumerate(errors, start=1)]
    user = "\n\n".join(
        [
            f"Занятие {lesson_number}. {title}",
            f"Уровень ученика: {level_label or 'не указан'}.",
            "Цели занятия:\n" + ("\n".join(goal_lines) if goal_lines else "- не указаны"),
            f"Нужно заданий: {question_count}. Ошибок в снимке: {len(errors)}.",
            "Ключи теста и внутренние пометки проверки — только для тебя. В report их не пересказывай и не давай готовое решение исходных задач.",
            "Условие и ключ каждого нового задания сделай максимально корректными. Перед JSON перепроверь каждое: реши заново и сверь буквы answers с этим решением.",
            "Ошибки помечены «Тест по теории» или «Практика». Ошибка теста обязательна: пункт в разборе и задание, если число заданий позволяет. Её нельзя заменить только практикой.",
            "Ошибки:\n\n" + ("\n\n".join(blocks) if blocks else "нет"),
        ]
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def generate_error_work(
    *,
    lesson_number: int,
    title: str,
    level_label: str,
    goals: list[str],
    errors: list[dict[str, Any]],
    question_count: int,
    model: str,
) -> PreparedWork:
    messages = build_messages(
        lesson_number=lesson_number,
        title=title,
        level_label=level_label,
        goals=goals,
        errors=errors,
        question_count=question_count,
    )
    client = _client()
    usage = ModelUsage()
    reason = ""
    cap = error_work_completion_cap()
    for _attempt in range(2):
        payload_messages = list(messages)
        if reason:
            payload_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Предыдущий ответ не принят: {reason}. "
                        f"Верни один JSON заново. Заданий должно набраться не меньше {question_count}. "
                        "Каждое задание реши заново и сверь ключ с вариантами."
                    ),
                }
            )
        kwargs = {"model": model, "messages": payload_messages, "max_tokens": cap}
        try:
            text, one = _request_model_text(client, kwargs, model)
            usage.add(one)
        except Exception as exc:
            absorb_usage(usage, exc)
            log.exception("error work generation failed")
            raise GenerationFailed(usage) from exc
        try:
            report, questions = parse_error_work_payload(text, question_count)
            return PreparedWork(report=report, questions=questions, usage=usage)
        except ValueError as exc:
            reason = str(exc)[:400]
            log.warning("error work payload rejected: %s", reason)
    raise GenerationFailed(usage)


def parse_error_work_payload(text: str, question_count: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = _load_json(text)
    report = _report(data.get("report") if isinstance(data.get("report"), dict) else data)
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list):
        raise ValueError("нет списка questions")
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        try:
            question = _question(raw)
        except ValueError:
            continue
        key = re.sub(r"\s+", " ", question["prompt"].casefold())
        if key in seen:
            continue
        seen.add(key)
        questions.append(question)
        if len(questions) >= question_count:
            break
    if len(questions) < question_count:
        raise ValueError(f"валидных заданий {len(questions)} из {question_count}")
    for index, question in enumerate(questions, start=1):
        question["code"] = f"Р{index}"
    return report, questions


_SYSTEM = """Ты учитель математики 9 класса (Беларусь, выпускной экзамен II ступени).
По ошибкам одного занятия готовишь работу над ошибками.

Верни только JSON без текста вокруг:
{
  "report": {
    "title": "короткий заголовок",
    "summary": "2–4 предложения: что путалось, без готовых ответов",
    "attention": [
      {"title": "на что смотреть", "text": "правило и как себя проверить, без ответа исходной задачи"}
    ]
  },
  "questions": [
    {
      "topic": "тема ошибки",
      "prompt": "новое условие, Markdown и LaTeX",
      "options": [
        {"letter": "а", "text": "..."},
        {"letter": "б", "text": "..."},
        {"letter": "в", "text": "..."},
        {"letter": "г", "text": "..."}
      ],
      "answers": ["б"],
      "multi": false
    }
  ]
}

Правила:
- В attention 3–6 пунктов. Пиши по-русски, спокойно и конкретно: какое правило, какой типичный сбой, что перепроверить.
- Не называй буквы верных вариантов и не давай готовое решение ни исходных задач, ни новых.
- Заданий не меньше запрошенного числа. Если ошибок меньше, дополнительные задания вариации тех же ошибок, а не новые темы.
- Ошибки теста по теории и ошибки практики входят одинаково. Неверный ответ в тесте нельзя пропустить, даже если практика по этой теме была верной и даже если в другой попытке теста ученик ответил правильно.
- Если ошибок больше, чем заданий, сначала закрой заданиями каждую ошибку теста по теории, затем самые частые ошибки практики. Остальные назови в разборе.
- У каждой ошибки из списка, и теста и практики, должен быть след в разборе.
- 4 варианта, буквы а б в г. Один верный, либо несколько, если в условии сказано выбрать все верные. Тогда multi true и в answers все верные буквы.
- Новые числа и формулировки, не копия исходного условия.
- Условие, варианты и ключ максимально корректны: задача решается, формулировка однозначна, данные согласованы, ровно те варианты верны, которые стоят в answers.
- Перед ответом перепроверь каждый тест отдельно: реши задание заново, не глядя на черновик ключа. Сверь, что буквы в answers дают именно этот результат. При multi отмечены все верные буквы и ни одной лишней. Если ключ не сходится, исправь условие или варианты и перепроверь ещё раз. В JSON клади только сверенный ключ.
- Десятичные дроби в LaTeX через точку. Перечисления через точку с запятой.
- Уровень ученика указан во входе: не завышай и не упрощай ниже него.
- В JSON экранируй обратные слэши LaTeX.
"""


def _format_error(index: int, item: dict[str, Any]) -> str:
    source = "Тест по теории" if item.get("source") == "test" else "Практика"
    lines = [
        f"Ошибка {index}. {source}, код {item.get('code') or '—'}.",
        f"Тема: {item.get('topic') or '—'}.",
        f"Сколько раз отвечено неверно: {int(item.get('wrong_count') or 1)}.",
        f"Позже это место было исправлено: {'да' if item.get('later_correct') else 'нет'}.",
        "Условие:",
        _clip(str(item.get("prompt") or ""), _TASK_LIMIT) or "—",
    ]
    options = item.get("options") or []
    if options:
        lines.append("Варианты:")
        for option in options:
            if isinstance(option, dict):
                lines.append(f"{option.get('letter')}) {_clip(str(option.get('text') or ''), 400)}")
    correct = item.get("correct") or []
    if correct:
        lines.append("Верный ключ, только для тебя: " + ", ".join(str(part) for part in correct))
    picks = item.get("picks") or []
    if picks:
        shown = [", ".join(part) if part else "пустой ответ" for part in picks]
        lines.append("Неверные выборы ученика: " + "; ".join(shown))
    for note in item.get("notes") or []:
        if not isinstance(note, dict):
            continue
        lines.append(
            "Неверная попытка {attempt}. Тип: {kind}. Внутренняя пометка: {detail}. Подсказка ученику: {feedback}".format(
                attempt=note.get("attempt") or "?",
                kind=note.get("error_kind") or "—",
                detail=note.get("error_detail") or "—",
                feedback=note.get("feedback") or "—",
            )
        )
    return "\n".join(lines)


def _load_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise ValueError("ответ не JSON")


def _report(data: dict[str, Any]) -> dict[str, Any]:
    title = _clip(str(data.get("title") or data.get("report_title") or ""), 140)
    summary = str(data.get("summary") or "").strip()
    if len(title) < 4:
        raise ValueError("короткий заголовок разбора")
    if len(summary) < 40:
        raise ValueError("слишком короткий разбор")
    if len(summary) > 1800:
        summary = summary[:1799].rstrip() + "…"
    raw_items = data.get("attention") or data.get("focus") or []
    if not isinstance(raw_items, list):
        raise ValueError("нет списка attention")
    attention = []
    for item in raw_items:
        if isinstance(item, str):
            text = item.strip()
            name = ""
        elif isinstance(item, dict):
            name = str(item.get("title") or item.get("name") or "").strip()
            text = str(item.get("text") or item.get("detail") or item.get("body") or "").strip()
        else:
            continue
        if len(text) < 20:
            continue
        attention.append({"title": _clip(name or "На что смотреть", 80), "text": text[:900]})
        if len(attention) >= 8:
            break
    if len(attention) < 2:
        raise ValueError("мало пунктов, на что смотреть")
    return {"title": title, "summary": summary, "attention": attention}


def _norm_letter(value: str) -> str:
    text = (value or "").strip().casefold()
    text = text.replace(")", "").replace(".", "").replace(" ", "")
    return _LETTER.get(text, "")


def _question(raw: dict[str, Any]) -> dict[str, Any]:
    prompt = str(raw.get("prompt") or raw.get("prompt_md") or raw.get("text") or "").strip()
    if len(prompt) < 8:
        raise ValueError("пустое условие")
    options = _options(raw.get("options"))
    answers = _answers(raw.get("answers"), options)
    if not answers:
        raise ValueError("нет верного варианта")
    topic = _clip(str(raw.get("topic") or ""), 80) or "Ошибка"
    return {
        "code": "",
        "topic": topic,
        "prompt": prompt,
        "options": options,
        "answers": answers,
        "multi": len(answers) > 1,
    }


def _options(raw: Any) -> list[dict[str, str]]:
    parsed: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        raise ValueError("нет вариантов")
    for item in raw:
        if isinstance(item, str):
            parsed.append(("", item.strip()))
        elif isinstance(item, dict):
            letter = _norm_letter(str(item.get("letter") or item.get("id") or ""))
            text = str(item.get("text") or item.get("text_md") or item.get("body") or "").strip()
            parsed.append((letter, text))
    parsed = [(letter, text) for letter, text in parsed if text]
    if len(parsed) < 4:
        raise ValueError("меньше четырёх вариантов")
    parsed = parsed[:4]
    letters = [letter for letter, _text in parsed]
    if len(set(letters)) == 4 and set(letters) <= set(_CYR):
        options = [{"letter": letter, "text": text} for letter, text in parsed]
    else:
        options = [{"letter": _CYR[index], "text": text} for index, (_letter, text) in enumerate(parsed)]
    bodies = [re.sub(r"\s+", " ", item["text"].casefold()) for item in options]
    if len(set(bodies)) < 4:
        raise ValueError("повторяющиеся варианты")
    return options


def _answers(raw: Any, options: list[dict[str, str]]) -> list[str]:
    if isinstance(raw, str):
        parts = re.split(r"[,;]\s*|\s+", raw.strip())
    elif isinstance(raw, list):
        parts = [str(item) for item in raw]
    else:
        parts = []
    allowed = {item["letter"] for item in options}
    found: list[str] = []
    for part in parts:
        letter = _norm_letter(part)
        if letter in allowed and letter not in found:
            found.append(letter)
    return sorted(found)
