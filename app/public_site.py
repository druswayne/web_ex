"""Данные публичных страниц: программа, демонстрационное занятие."""

from __future__ import annotations

from .content import display_prompt, get_bank, normalize_opt, render_markdown

DEMO_LESSON = 1
DEMO_QUESTION_LIMIT = 3
DEMO_TASK_LIMIT = 2
ROLE_LABELS = {
    "review": "Повторение",
    "exam_train": "Экзаменационный тренинг",
}

# Группы заданий курса. Это не прогноз балла на экзамене.
LEVEL_FIT = {
    "1-2": "Если нужно спокойно вспомнить базовые действия.",
    "3-4": "Если основные правила уже знакомы и их стоит закрепить.",
    "5-6": "Если получаются типовые задачи в несколько шагов.",
    "7-8": "Если темы уже соединяются и хочется плотнее потренироваться.",
    "9-10": "Если нужны задания повышенной сложности.",
}
LEVEL_BLURBS = {
    "1-2": "Короткие вычисления и простые понятия.",
    "3-4": "Основные навыки без длинных комбинаций.",
    "5-6": "Типовые задачи, где решение состоит из нескольких шагов.",
    "7-8": "Сочетания тем и более длинные рассуждения.",
    "9-10": "Комплексная практика. Уровень можно сменить в кабинете.",
}


def program_groups() -> list[dict]:
    groups: list[dict] = []
    for lesson in get_bank().all():
        section = lesson.section or "Занятия"
        if not groups or groups[-1]["section"] != section:
            groups.append({"section": section, "lessons": []})
        groups[-1]["lessons"].append(lesson)
    return groups


def _demo_lesson():
    try:
        return get_bank().get(DEMO_LESSON)
    except KeyError:
        lessons = get_bank().all()
        return lessons[0] if lessons else None


def demo_view() -> dict | None:
    lesson = _demo_lesson()
    if lesson is None:
        return None
    questions_src = lesson.test_for("1-2")[:DEMO_QUESTION_LIMIT]
    tasks_src = lesson.practice_for("1-2")[:DEMO_TASK_LIMIT]
    return {
        "lesson": lesson,
        "theory_html": render_markdown(_theory_excerpt(lesson.theory_md), decorate_theory=True),
        "questions": [
            {
                "q": q,
                "prompt_html": render_markdown(display_prompt(q.prompt_md, q.multi)),
                "options": [(letter, render_markdown(text)) for letter, text in q.options],
            }
            for q in questions_src
        ],
        "tasks": [
            {"code": task.code, "html": render_markdown(task.text_md)}
            for task in tasks_src
            if (task.text_md or "").strip()
        ],
    }


def grade_demo(form) -> tuple[list[dict], int]:
    """Сверяет ответы демонстрационного теста. Верные буквы не отдаются до отправки."""
    lesson = _demo_lesson()
    if lesson is None:
        return [], 0
    rows: list[dict] = []
    correct = 0
    for q in lesson.test_for("1-2")[:DEMO_QUESTION_LIMIT]:
        picked = []
        for raw in form.getlist(q.code):
            letter = normalize_opt(raw)
            if letter and letter not in picked:
                picked.append(letter)
        expected = [normalize_opt(a) for a in q.answers if normalize_opt(a)]
        ok = bool(expected) and sorted(picked) == sorted(expected)
        if ok:
            correct += 1
        rows.append(
            {
                "code": q.code,
                "ok": ok,
                "picked": picked,
                "answers": expected,
            }
        )
    return rows, correct


def _theory_excerpt(source: str) -> str:
    text = source or ""
    marker = "### Мини-тема"
    first = text.find(marker)
    if first < 0:
        return text[:6000]
    second = text.find(marker, first + len(marker))
    if second < 0:
        return text[first:first + 6000]
    return text[first:second].strip()
