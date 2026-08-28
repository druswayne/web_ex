from __future__ import annotations

import re
from pathlib import Path

from .config import CONTENT_ROOT, LEVEL_BANDS, LEVEL_LABELS
from .content import (
    CYR_OPTS,
    LEVEL_HEAD_RE,
    TEST_Q_RE,
    PracticeTask,
    TestQuestion,
    get_bank,
    normalize_band,
    normalize_opt,
    reload_bank,
)

THEORY_HEAD = "## Теория к занятию"
CLASS_HEAD = "## Самостоятельная работа"
HOMEWORK_HEAD = "## Домашнее задание"
CHECKLIST_HEAD = "## Чек-лист"
CLASS_ANS_HEAD = "## Ответы к самостоятельной работе"
HW_ANS_HEAD = "## Ответы к домашнему заданию"

CODE_PREFIX_RE = re.compile(r"^[СДCDсд]\d+\.?\s*")


def lesson_dir(root: Path | None = None) -> Path:
    return Path(root or CONTENT_ROOT) / "lessons"


def lesson_paths(number: int, root: Path | None = None) -> dict[str, Path]:
    d = lesson_dir(root)
    nn = f"{number:02d}"
    return {
        "md": d / f"{nn}.md",
        "answers": d / f"{nn}_answers.md",
        "test": d / f"{nn}_test.md",
        "test_answers": d / f"{nn}_test_answers.md",
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n").rstrip() + "\n", encoding="utf-8")


def _finish_save(root: Path | None) -> None:
    if root is None or Path(root).resolve() == Path(CONTENT_ROOT).resolve():
        reload_bank()


def _replace_section(text: str, heading: str, stops: list[str], new_body: str) -> str:
    idx = text.find(heading)
    body = (new_body or "").strip()
    if idx < 0:
        extra = f"\n\n{heading}\n\n{body}\n" if body else f"\n\n{heading}\n"
        return text.rstrip() + extra
    rest = text[idx + len(heading) :]
    cut = len(rest)
    for stop in stops:
        j = rest.find("\n" + stop)
        if j >= 0:
            cut = min(cut, j)
    return text[:idx] + heading + "\n\n" + body + "\n" + rest[cut:]


def _level_heading(band: str) -> str:
    return f"### Уровень {LEVEL_LABELS.get(band, band)}"


def _replace_level_block(section: str, band: str, new_block: str) -> str:
    heading = _level_heading(band)
    block = heading + "\n\n" + (new_block or "").strip() + "\n"
    matches = list(LEVEL_HEAD_RE.finditer(section or ""))
    for i, m in enumerate(matches):
        if normalize_band(m.group(1)) != band:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        return section[:start] + block + section[end:]
    if not (section or "").strip():
        return block
    return section.rstrip() + "\n\n" + block


def _preamble_before_questions(text: str) -> str:
    if not (text or "").strip():
        return ""
    starts: list[int] = []
    level = LEVEL_HEAD_RE.search(text)
    if level:
        starts.append(level.start())
    question = TEST_Q_RE.search(text)
    if question:
        starts.append(question.start())
    if not starts:
        return text.strip()
    return text[: min(starts)].strip()


def _strip_task_code(title: str) -> str:
    t = (title or "").strip().strip("*").strip()
    return CODE_PREFIX_RE.sub("", t).strip()


def format_practice_block(tasks: list[PracticeTask], letter: str) -> str:
    parts: list[str] = []
    for i, task in enumerate(tasks, start=1):
        code = f"{letter}{i}"
        rest = _strip_task_code(task.title)
        head = f"**{code}. {rest}**" if rest else f"**{code}.**"
        body = (task.text_md or "").strip()
        parts.append(f"{head}\n\n{body}".rstrip() if body else head)
    return "\n\n".join(parts)


def format_practice_answers(tasks: list[PracticeTask], letter: str) -> str:
    lines: list[str] = []
    for i, task in enumerate(tasks, start=1):
        ans = (task.answer or "").strip()
        if not ans:
            continue
        lines.append(f"{letter}{i} — {ans}")
    return "\n".join(lines)


def format_questions_md(questions: list[TestQuestion]) -> str:
    parts: list[str] = []
    for q in questions:
        head = f"## Т{q.number}."
        if q.topic:
            head += f" *({q.topic})*"
        lines = [head, "", (q.prompt_md or "").strip(), ""]
        for letter, text in q.options:
            lines.append(f"{letter}) {text}".rstrip())
        parts.append("\n".join(lines).strip())
    return "\n\n".join(parts)


def format_test_answers_md(questions: list[TestQuestion]) -> str:
    lines: list[str] = []
    for q in questions:
        letters = ", ".join(q.answers)
        if letters:
            lines.append(f"**Т{q.number}.** {letters}")
        else:
            lines.append(f"**Т{q.number}.**")
    return "\n".join(lines)


def _compose_banded_document(preamble: str, blocks: dict[str, str], fallback_title: str) -> str:
    head = (preamble or "").strip() or fallback_title
    parts = [head, ""]
    for band in LEVEL_BANDS:
        body = (blocks.get(band) or "").strip()
        parts.append(_level_heading(band))
        parts.append("")
        if body:
            parts.append(body)
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def tasks_from_form(form, prefix: str, *, kind: str, letter: str, slot_base: int) -> list[PracticeTask]:
    count = int(form.get(f"{prefix}_count") or 0)
    tasks: list[PracticeTask] = []
    n = 0
    for i in range(1, count + 1):
        title = (form.get(f"{prefix}{i}_title") or "").strip()
        text = (form.get(f"{prefix}{i}_text") or "").strip()
        answer = (form.get(f"{prefix}{i}_answer") or "").strip()
        if not title and not text and not answer:
            continue
        n += 1
        code = f"{letter}{n}"
        rest = _strip_task_code(title)
        full_title = f"{code}. {rest}" if rest else f"{code}."
        tasks.append(
            PracticeTask(
                code=code,
                slot=slot_base + n,
                title=full_title,
                text_md=text,
                answer=answer,
                kind=kind,
            )
        )
    return tasks


def questions_from_form(form) -> list[TestQuestion]:
    count = int(form.get("q_count") or 0)
    questions: list[TestQuestion] = []
    n = 0
    for i in range(1, count + 1):
        prompt = (form.get(f"q{i}_prompt") or "").strip()
        topic = (form.get(f"q{i}_topic") or "").strip()
        option_vals = [(v or "").strip() for v in form.getlist(f"q{i}_opt")]
        option_vals = [v for v in option_vals if v]
        answers_raw = form.getlist(f"q{i}_ans")
        if not prompt and not option_vals and not topic:
            continue
        n += 1
        options: list[tuple[str, str]] = []
        for letter, val in zip(CYR_OPTS, option_vals):
            options.append((letter, val))
        allowed = {o[0] for o in options}
        answers = []
        for raw in answers_raw:
            letter = normalize_opt(raw)
            if letter in allowed and letter not in answers:
                answers.append(letter)
        multi = form.get(f"q{i}_multi") == "1" or len(answers) > 1
        questions.append(
            TestQuestion(
                code=f"T{n}",
                number=n,
                topic=topic,
                prompt_md=prompt,
                options=options,
                answers=answers,
                multi=multi,
            )
        )
    return questions


def save_theory(number: int, body: str, root: Path | None = None) -> None:
    paths = lesson_paths(number, root)
    if not paths["md"].is_file():
        raise FileNotFoundError(f"Нет файла занятия {number}")
    text = paths["md"].read_text(encoding="utf-8")
    text = _replace_section(text, THEORY_HEAD, [CLASS_HEAD], body)
    _write(paths["md"], text)
    _finish_save(root)


def save_test_band(number: int, band: str, questions: list[TestQuestion], root: Path | None = None) -> None:
    band = normalize_band(band)
    if band not in LEVEL_BANDS:
        raise ValueError("Неизвестный уровень")
    paths = lesson_paths(number, root)
    lesson = get_bank().get(number)
    test_md = paths["test"].read_text(encoding="utf-8") if paths["test"].is_file() else ""
    ans_md = paths["test_answers"].read_text(encoding="utf-8") if paths["test_answers"].is_file() else ""
    preamble = _preamble_before_questions(test_md)
    if not preamble:
        preamble = (
            f"# Тест по теории. Занятие {number}. {lesson.title}\n\n"
            f"**Заданий:** {len(questions)}\n"
            "**Формат:** 4 варианта ответа (а–г). Верных может быть один или несколько."
        )
    else:
        preamble = re.sub(
            r"\*\*Заданий:\*\*\s*\d+",
            f"**Заданий:** {len(questions)}",
            preamble,
            count=1,
        )
    ans_preamble = _preamble_before_questions(ans_md)
    if not ans_preamble:
        ans_preamble = f"# Ответы к тесту по теории. Занятие {number}. {lesson.title}"

    q_blocks: dict[str, str] = {}
    a_blocks: dict[str, str] = {}
    for b in LEVEL_BANDS:
        qs = questions if b == band else lesson.test_for(b)
        q_blocks[b] = format_questions_md(qs)
        a_blocks[b] = format_test_answers_md(qs)

    _write(
        paths["test"],
        _compose_banded_document(
            preamble,
            q_blocks,
            f"# Тест по теории. Занятие {number}. {lesson.title}",
        ),
    )
    _write(
        paths["test_answers"],
        _compose_banded_document(
            ans_preamble,
            a_blocks,
            f"# Ответы к тесту по теории. Занятие {number}. {lesson.title}",
        ),
    )
    _finish_save(root)


def save_practice_band(
    number: int,
    band: str,
    class_tasks: list[PracticeTask],
    homework_tasks: list[PracticeTask],
    root: Path | None = None,
) -> None:
    band = normalize_band(band)
    if band not in LEVEL_BANDS:
        raise ValueError("Неизвестный уровень")
    paths = lesson_paths(number, root)
    if not paths["md"].is_file():
        raise FileNotFoundError(f"Нет файла занятия {number}")
    md = paths["md"].read_text(encoding="utf-8")
    class_body = format_practice_block(class_tasks, "С")
    hw_body = format_practice_block(homework_tasks, "Д")

    class_section = _section_inner(md, CLASS_HEAD, [HOMEWORK_HEAD, CHECKLIST_HEAD])
    class_section = _replace_level_block(class_section, band, class_body)
    md = _replace_section(md, CLASS_HEAD, [HOMEWORK_HEAD, CHECKLIST_HEAD], class_section)

    hw_section = _section_inner(md, HOMEWORK_HEAD, [CHECKLIST_HEAD])
    hw_section = _replace_level_block(hw_section, band, hw_body)
    md = _replace_section(md, HOMEWORK_HEAD, [CHECKLIST_HEAD], hw_section)
    _write(paths["md"], md)

    ans = paths["answers"].read_text(encoding="utf-8") if paths["answers"].is_file() else ""
    if not ans.strip():
        lesson = get_bank().get(number)
        ans = f"# Ответы к занятию {number}. {lesson.title}\n"
    class_ans_section = _section_inner(ans, CLASS_ANS_HEAD, [HW_ANS_HEAD])
    class_ans_section = _replace_level_block(
        class_ans_section, band, format_practice_answers(class_tasks, "С")
    )
    if CLASS_ANS_HEAD not in ans:
        ans = ans.rstrip() + f"\n\n{CLASS_ANS_HEAD}\n"
    ans = _replace_section(ans, CLASS_ANS_HEAD, [HW_ANS_HEAD], class_ans_section)

    hw_ans_section = _section_inner(ans, HW_ANS_HEAD, [])
    hw_ans_section = _replace_level_block(
        hw_ans_section, band, format_practice_answers(homework_tasks, "Д")
    )
    if HW_ANS_HEAD not in ans:
        ans = ans.rstrip() + f"\n\n{HW_ANS_HEAD}\n"
    ans = _replace_section(ans, HW_ANS_HEAD, [], hw_ans_section)
    _write(paths["answers"], ans)
    _finish_save(root)


def _section_inner(text: str, start: str, stops: list[str]) -> str:
    idx = text.find(start)
    if idx < 0:
        return ""
    rest = text[idx + len(start) :]
    cut = len(rest)
    for stop in stops:
        j = rest.find("\n" + stop)
        if j >= 0:
            cut = min(cut, j)
    return rest[:cut].strip()
