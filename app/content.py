from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import markdown
from markupsafe import Markup

from .config import CONTENT_ROOT, LEVEL_BANDS

LATIN_OPT = {"a": "а", "b": "б", "c": "в", "d": "г", "e": "д"}
CYR_OPTS = ("а", "б", "в", "г", "д")
LEVEL_HEAD_RE = re.compile(
    r"^###\s+Уровень\s+(1[–-]2|3[–-]4|5[–-]6|7[–-]8|9[–-]10)\s+балл",
    re.M,
)
WALK_COLLECTION_HEAD_RE = re.compile(
    r"^(###\s+Р\d+)\.\s*Сборник,\s*вариант\s+\d+,\s*задание\s+\d+\.?\s*$",
    re.M | re.I,
)
TASK_SPLIT_RE = re.compile(r"(?=\*\*[СД]\d+\.)")
TASK_HEAD_RE = re.compile(r"^\*\*([СД](\d+)\.(?:[^*]|\*(?!\*))*)\*\*\s*", re.S)
NUM_TASK_SPLIT_RE = re.compile(r"(?m)(?=^\d+\.\s)")
TEST_Q_RE = re.compile(r"^##\s+(Т(\d+))\.\s*(?:\*\((.+?)\)\*)?\s*$", re.M)
TEST_ANS_RE = re.compile(
    r"\*\*Т(\d+)\.\*\*\s*([а-джa-eа-яё,\s]+)",
    re.I,
)
PRAC_ANS_SPLIT_RE = re.compile(
    r"\*{0,2}([СД])(\d+)\.?\*{0,2}\s*[—–:-]?\s*",
)
HOMEWORK_SLOT_BASE = 100
ANS_LETTER = {"С": "C", "Д": "D"}
MATH_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.S)
MATH_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\\])+\$")
# В math `.` — пунктуация: `$2.5$` рисуется как «2, 5». `{.}` оставляет точку дроби.
DECIMAL_DOT_RE = re.compile(r"(\d)\.(\d)")
try:
    MD = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "nl2br", "pymdownx.tilde"],
        output_format="html",
    )
except ImportError:
    MD = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html",
    )


def normalize_band(raw: str) -> str:
    t = (raw or "").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", "", t)
    m = re.search(r"(1-2|3-4|5-6|7-8|9-10)", t)
    return m.group(1) if m else t


def normalize_opt(letter: str) -> str:
    s = (letter or "").strip().lower()
    return LATIN_OPT.get(s, s)


def parse_opt_list(raw: str) -> list[str]:
    parts = re.split(r"[,\s;]+", (raw or "").strip())
    out: list[str] = []
    for p in parts:
        p = re.sub(r"[^а-яa-zё]", "", p.lower())
        if not p:
            continue
        n = normalize_opt(p)
        if n and n not in out:
            out.append(n)
    return out


def _section_after(text: str, start: str, stops: list[str]) -> str:
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


def _decimal_dot_ordinary(math: str) -> str:
    math = math.replace("{,}", ".")
    return DECIMAL_DOT_RE.sub(r"\1{.}\2", math)


def _protect_math(text: str) -> tuple[str, dict[str, str]]:
    store: dict[str, str] = {}

    def hold(m: re.Match[str]) -> str:
        key = f"@@MATH{len(store)}@@"
        store[key] = _decimal_dot_ordinary(m.group(0))
        return key

    text = MATH_BLOCK_RE.sub(hold, text)
    text = MATH_INLINE_RE.sub(hold, text)
    return text, store


def strip_walk_collection_heads(text: str) -> str:
    return WALK_COLLECTION_HEAD_RE.sub(r"\1.", text or "")


def render_markdown(source: str, *, decorate_theory: bool = False) -> str:
    if not (source or "").strip():
        return ""
    text = source.replace("](figures/", "](/media/figures/")
    text, store = _protect_math(text)
    MD.reset()
    html = MD.convert(text)
    for key, val in store.items():
        html = html.replace(key, val)
    if decorate_theory:
        html = decorate_theory_html(html)
    return html


HEAD_RE = re.compile(r"<h([2-4])(?:\s[^>]*)?>.*?</h\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SKIP_HEAD_RE = re.compile(
    r"^(?:[а-яёa-z]\)|\d+[\.)]|первый\b|второй\b|третий\b)",
    re.I,
)
THEORY_H2_RE = re.compile(
    r"<h2(?:\s[^>]*)?>\s*Теория к занятию\s*</h2>\s*",
    re.I,
)
MATH_PARA_RE = re.compile(r"<p>\s*(\$\$(?:\\.|[^$])+\$\$)\s*</p>", re.S)
LEAD_P_RE = re.compile(
    r"<p>(\s*<(?:strong|em)>(Условие|Решение|Ответ|Дано)[:.]?\s*</(?:strong|em)>)",
    re.I,
)
LABEL_ONLY_P_RE = re.compile(
    r"<p>\s*<(strong|em)>([^<]+)</\1>\s*(?:<br\s*/?>)?\s*</p>",
    re.I,
)
LEAD_NAMES = {"условие", "решение", "ответ", "дано"}
KIND_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ловушк", re.I), "trap"),
    (re.compile(r"нельзя путать", re.I), "warn"),
    (re.compile(r"формул|зависимост", re.I), "formula"),
    (re.compile(r"определен", re.I), "definition"),
    (re.compile(r"теорем", re.I), "theorem"),
    (re.compile(r"аксиом", re.I), "axiom"),
    (re.compile(r"свойств", re.I), "property"),
    (re.compile(r"признак", re.I), "feature"),
    (re.compile(r"следстви", re.I), "corollary"),
    (re.compile(r"алгоритм|построен", re.I), "algorithm"),
    (re.compile(r"правил", re.I), "rule"),
    (re.compile(r"простыми словами", re.I), "plain"),
    (re.compile(r"^факт", re.I), "fact"),
    (re.compile(r"замечан", re.I), "note"),
    (re.compile(r"доказательств", re.I), "proof"),
    (re.compile(r"^дано\b|^условие\b", re.I), "given"),
    (re.compile(r"^решение\b", re.I), "solve"),
    (re.compile(r"^ответ\b", re.I), "answer"),
    (re.compile(r"разбор ключевых|ключев\w+\s+задан", re.I), "examples"),
    (re.compile(r"самопроверк|мини-проверк|мини-практик", re.I), "check"),
    (re.compile(r"набор для практики|разреш[её]нн", re.I), "check"),
    (re.compile(r"итог|памятк|сводка", re.I), "summary"),
    (re.compile(r"^как\b|^чтобы\b", re.I), "algorithm"),
    (re.compile(r"пример", re.I), "example"),
    (re.compile(r"^р\d+|^\s*задани", re.I), "example"),
]
LEAD_KIND = {
    "условие": "given",
    "дано": "given",
    "решение": "solve",
    "ответ": "answer",
}


def _plain_heading(html: str) -> str:
    inner = re.sub(r"^<h[2-4](?:\s[^>]*)?>|</h[2-4]>$", "", html.strip(), flags=re.I)
    text = TAG_RE.sub("", inner)
    return re.sub(r"\s+", " ", text).strip()


def classify_theory_heading(level: int, title: str) -> str | None:
    raw = (title or "").strip()
    if not raw:
        return None
    if level == 2 and raw.lower().startswith("теория"):
        return None
    if re.search(r"мини-тема", raw, re.I):
        return None
    for rx, kind in KIND_RULES:
        if rx.search(raw):
            return kind
    if level == 4 and not SKIP_HEAD_RE.match(raw):
        return "aside"
    return None


def _promote_label_headings(html: str) -> str:
    def repl(m: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", m.group(2)).strip()
        key = re.sub(r"[.:]\s*$", "", label).strip().lower()
        if key in LEAD_NAMES:
            return m.group(0)
        return f"<h4>{label}</h4>"

    return LABEL_ONLY_P_RE.sub(repl, html)


def decorate_theory_html(html: str) -> str:
    if not (html or "").strip():
        return html or ""
    html = THEORY_H2_RE.sub("", html, count=1)
    html = _promote_label_headings(html)
    marks = list(HEAD_RE.finditer(html))
    headings: list[dict[str, Any]] = []
    for m in marks:
        level = int(m.group(1))
        title = _plain_heading(m.group(0))
        kind = classify_theory_heading(level, title)
        headings.append({"start": m.start(), "level": level, "kind": kind})
    events: list[tuple[int, int, int, str, str]] = []
    for i, h in enumerate(headings):
        if not h["kind"]:
            continue
        wrap_end = len(html)
        for nxt in headings[i + 1 :]:
            if nxt["level"] <= h["level"]:
                wrap_end = nxt["start"]
                break
        events.append((h["start"], 1, h["level"], "open", h["kind"]))
        events.append((wrap_end, 0, -h["level"], "close", h["kind"]))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    out: list[str] = []
    last = 0
    for pos, _phase, _tie, action, kind in events:
        out.append(html[last:pos])
        if action == "open":
            out.append(f'<section class="tbox tbox-{kind}">')
        else:
            out.append("</section>")
        last = pos
    out.append(html[last:])
    html = "".join(out)
    html = MATH_PARA_RE.sub(r'<div class="t-math">\1</div>', html)

    def lead_class(m: re.Match[str]) -> str:
        kind = LEAD_KIND.get(m.group(2).lower(), "aside")
        return f'<p class="t-lead t-lead-{kind}">{m.group(1)}'

    return LEAD_P_RE.sub(lead_class, html)


MULTI_LEADIN_RE = re.compile(
    r"^Выберите все верные (?:ответы|утверждения|записи)\.?\s*",
    re.I,
)


def display_prompt(prompt_md: str, multi: bool) -> str:
    text = (prompt_md or "").strip()
    if multi:
        text = MULTI_LEADIN_RE.sub("", text).strip()
    return text


@dataclass
class PracticeTask:
    code: str
    slot: int
    title: str
    text_md: str
    answer: str = ""
    kind: str = "class"


@dataclass
class TestQuestion:
    code: str
    number: int
    topic: str
    prompt_md: str
    options: list[tuple[str, str]]
    answers: list[str] = field(default_factory=list)
    multi: bool = False


@dataclass
class LessonContent:
    number: int
    title: str
    section: str
    date_label: str
    goals: list[str]
    theory_md: str
    practice: dict[str, list[PracticeTask]]
    tests: dict[str, list[TestQuestion]]
    checklist: list[str] = field(default_factory=list)
    subtopics: list[str] = field(default_factory=list)

    @property
    def theory_html(self) -> Markup:
        return Markup(
            render_markdown(
                strip_walk_collection_heads(self.theory_md),
                decorate_theory=True,
            )
        )

    @property
    def theory_body(self) -> str:
        text = self.theory_md or ""
        text = re.sub(r"^##\s+Теория к занятию\s*", "", text).strip()
        return text

    def test_for(self, band: str | None = None) -> list[TestQuestion]:
        key = normalize_band(band or "")
        if key in self.tests:
            return self.tests[key]
        for b in LEVEL_BANDS:
            if self.tests.get(b):
                return self.tests[b]
        return []

    @property
    def test(self) -> list[TestQuestion]:
        return self.test_for(None)

    def practice_for(self, band: str | None = None) -> list[PracticeTask]:
        key = normalize_band(band or "")
        if key in self.practice:
            return self.practice[key]
        return self.practice.get("1-2") or []


def _is_level_choice_goal(text: str) -> bool:
    t = (text or "").lower().replace("–", "-").replace("—", "-")
    if "1-2" not in t:
        return False
    if not any(x in t for x in ("3-4", "5-6", "7-8", "9-10")):
        return False
    return any(w in t for w in ("блок", "уровн", "выбир", "выбра"))


def _parse_goals(md: str) -> list[str]:
    block = _section_after(md, "## Цели занятия", ["## Тайминг", "## Теория к занятию"])
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("- "):
            continue
        item = s[2:].strip()
        if _is_level_choice_goal(item):
            continue
        items.append(item)
    return items


def _parse_checklist(md: str) -> list[str]:
    block = _section_after(md, "## Чек-лист", ["## "])
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        m = re.match(r"^- \[[ xX]\]\s*(.+)$", s)
        if m:
            items.append(m.group(1).strip())
    return items


def _split_level_blocks(section: str) -> dict[str, str]:
    matches = list(LEVEL_HEAD_RE.finditer(section))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        band = normalize_band(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
        out[band] = section[start:end].strip()
    return out


def _parse_tasks(
    block: str,
    *,
    code_prefix: str = "C",
    slot_base: int = 0,
    kind: str = "class",
) -> list[PracticeTask]:
    starred = _parse_star_tasks(block, slot_base=slot_base, kind=kind)
    if starred:
        return starred
    return _parse_numbered_tasks(
        block, code_prefix=code_prefix, slot_base=slot_base, kind=kind
    )


def _clean_task_body(body: str) -> str:
    body = re.sub(r"^---\s*$", "", (body or "").strip(), flags=re.M).strip()
    if body.endswith("---"):
        body = body[: -len("---")].strip()
    return body


def _parse_star_tasks(
    block: str, *, slot_base: int = 0, kind: str = "class"
) -> list[PracticeTask]:
    tasks: list[PracticeTask] = []
    for chunk in TASK_SPLIT_RE.split(block or ""):
        chunk = chunk.strip()
        if not (chunk.startswith("**С") or chunk.startswith("**Д")):
            continue
        hm = TASK_HEAD_RE.match(chunk)
        if not hm:
            continue
        num = int(hm.group(2))
        code_letter = "D" if hm.group(1).startswith("Д") else "C"
        task_kind = "homework" if code_letter == "D" else kind
        title = hm.group(1).strip()
        body = _clean_task_body(chunk[hm.end() :])
        tasks.append(
            PracticeTask(
                code=f"{code_letter}{num}",
                slot=slot_base + num,
                title=title,
                text_md=body,
                kind=task_kind,
            )
        )
    return tasks


def _parse_numbered_tasks(
    block: str,
    *,
    code_prefix: str = "C",
    slot_base: int = 0,
    kind: str = "class",
) -> list[PracticeTask]:
    tasks: list[PracticeTask] = []
    for chunk in NUM_TASK_SPLIT_RE.split(block or ""):
        chunk = chunk.strip()
        m = re.match(r"^(\d+)\.\s+(.*)$", chunk, re.S)
        if not m:
            continue
        num = int(m.group(1))
        rest = m.group(2).strip()
        title_m = re.match(r"^(\*[^*]+\*)\s*(.*)$", rest, re.S)
        if title_m:
            title = f"{code_prefix}{num}. {title_m.group(1).strip('*').strip()}"
            body = _clean_task_body(title_m.group(2))
        else:
            title = f"{code_prefix}{num}."
            body = _clean_task_body(rest)
        tasks.append(
            PracticeTask(
                code=f"{code_prefix}{num}",
                slot=slot_base + num,
                title=title,
                text_md=body,
                kind=kind,
            )
        )
    return tasks


def _parse_answer_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    parts = PRAC_ANS_SPLIT_RE.split(text.strip())
    i = 1
    while i + 2 < len(parts):
        prefix = ANS_LETTER.get(parts[i], "C")
        mapping[f"{prefix}{int(parts[i + 1])}"] = parts[i + 2].strip().rstrip(";").strip()
        i += 3
    return mapping


def _parse_practice_answers(
    ans_md: str,
    heading: str,
    stops: list[str],
) -> dict[str, dict[str, str]]:
    block = _section_after(ans_md, heading, stops)
    if not block and heading == "## Ответы к самостоятельной работе":
        block = ans_md
    result: dict[str, dict[str, str]] = {}
    for band, text in _split_level_blocks(block).items():
        result[band] = _parse_answer_map(text)
    return result


def _parse_options(body: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(re.finditer(r"(?:^|\n)\s*([абвгдеabcde])\)\s*", body))
    if not matches:
        return body.strip(), []
    prompt = body[: matches[0].start()].strip()
    options: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        letter = normalize_opt(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        val = body[start:end].strip().rstrip(";.").strip()
        val = val.rstrip(";").strip()
        options.append((letter, val))
    return prompt, options


def _parse_tests_by_band(test_md: str, answers_md: str) -> dict[str, list[TestQuestion]]:
    test_blocks = _split_level_blocks(test_md)
    ans_blocks = _split_level_blocks(answers_md)
    if not test_blocks:
        shared = _parse_test(test_md, answers_md)
        return {band: list(shared) for band in LEVEL_BANDS}
    out: dict[str, list[TestQuestion]] = {}
    for band in LEVEL_BANDS:
        block = test_blocks.get(band, "")
        answers = ans_blocks.get(band, "")
        if not ans_blocks:
            answers = answers_md
        out[band] = _parse_test(block, answers) if block else []
    for extra, block in test_blocks.items():
        if extra not in out:
            out[extra] = _parse_test(block, ans_blocks.get(extra, ""))
    return out


def _parse_test(test_md: str, answers_md: str) -> list[TestQuestion]:
    keys: dict[int, list[str]] = {}
    for m in TEST_ANS_RE.finditer(answers_md or ""):
        keys[int(m.group(1))] = parse_opt_list(m.group(2))
    questions: list[TestQuestion] = []
    marks = list(TEST_Q_RE.finditer(test_md or ""))
    for i, m in enumerate(marks):
        num = int(m.group(2))
        topic = (m.group(3) or "").strip()
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(test_md)
        raw = test_md[start:end].strip()
        prompt, options = _parse_options(raw)
        answers = keys.get(num, [])
        low = (prompt + " " + raw).lower()
        multi = ("выберите все" in low) or (len(answers) > 1)
        questions.append(
            TestQuestion(
                code=f"T{num}",
                number=num,
                topic=topic,
                prompt_md=prompt,
                options=options,
                answers=answers,
                multi=multi,
            )
        )
    return questions


def _load_roadmap(root: Path) -> dict[int, dict[str, Any]]:
    path = root / "roadmap.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, dict[str, Any]] = {}
    for item in data.get("lessons") or []:
        n = int(item.get("number") or 0)
        if n:
            out[n] = item
    return out


def load_lesson(root: Path, number: int, meta: dict[str, Any] | None = None) -> LessonContent | None:
    path = root / "lessons" / f"{number:02d}.md"
    if not path.is_file():
        return None
    md = path.read_text(encoding="utf-8")
    title_m = re.match(r"#\s+Занятие\s+\d+\.\s*(.+)", md)
    title = (title_m.group(1).strip() if title_m else "") or (meta or {}).get("title") or f"Занятие {number}"
    theory = _section_after(
        md,
        "## Теория к занятию",
        ["## Самостоятельная работа"],
    )
    if theory:
        theory = "## Теория к занятию\n\n" + strip_walk_collection_heads(theory)
    class_block = _section_after(
        md,
        "## Самостоятельная работа",
        ["## Домашнее задание", "## Чек-лист"],
    )
    homework_block = _section_after(
        md,
        "## Домашнее задание",
        ["## Чек-лист"],
    )
    ans_path = root / "lessons" / f"{number:02d}_answers.md"
    ans_md = ans_path.read_text(encoding="utf-8") if ans_path.is_file() else ""
    class_answers = _parse_practice_answers(
        ans_md,
        "## Ответы к самостоятельной работе",
        ["## Ответы к домашнему заданию", "## "],
    )
    hw_answers = _parse_practice_answers(
        ans_md,
        "## Ответы к домашнему заданию",
        ["## "],
    )
    class_blocks = _split_level_blocks(class_block)
    hw_blocks = _split_level_blocks(homework_block)
    bands = [b for b in LEVEL_BANDS if b in class_blocks or b in hw_blocks]
    for extra in class_blocks.keys() | hw_blocks.keys():
        if extra not in bands:
            bands.append(extra)
    practice: dict[str, list[PracticeTask]] = {}
    for band in bands:
        class_tasks = _parse_tasks(class_blocks.get(band, ""), kind="class")
        for t in class_tasks:
            t.answer = class_answers.get(band, {}).get(t.code, "")
        hw_tasks = _parse_tasks(
            hw_blocks.get(band, ""),
            code_prefix="D",
            slot_base=HOMEWORK_SLOT_BASE,
            kind="homework",
        )
        for t in hw_tasks:
            t.answer = hw_answers.get(band, {}).get(t.code, "")
        practice[band] = class_tasks + hw_tasks
    test_path = root / "lessons" / f"{number:02d}_test.md"
    test_ans_path = root / "lessons" / f"{number:02d}_test_answers.md"
    test_md = test_path.read_text(encoding="utf-8") if test_path.is_file() else ""
    test_ans = test_ans_path.read_text(encoding="utf-8") if test_ans_path.is_file() else ""
    meta = meta or {}
    return LessonContent(
        number=number,
        title=title,
        section=str(meta.get("section") or ""),
        date_label=str(meta.get("date_label") or ""),
        goals=_parse_goals(md),
        theory_md=theory,
        practice=practice,
        tests=_parse_tests_by_band(test_md, test_ans),
        checklist=_parse_checklist(md),
        subtopics=list(meta.get("subtopics") or []),
    )


class ContentBank:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or CONTENT_ROOT)
        self.lessons: dict[int, LessonContent] = {}
        self.exam_title = "Подготовка к выпускному экзамену"
        self._load()

    def _load(self) -> None:
        roadmap = _load_roadmap(self.root)
        rp = self.root / "roadmap.json"
        if rp.is_file():
            data = json.loads(rp.read_text(encoding="utf-8"))
            self.exam_title = data.get("exam") or self.exam_title
        numbers = sorted(roadmap) if roadmap else list(range(1, 36))
        if not numbers:
            numbers = list(range(1, 36))
        for n in numbers:
            lesson = load_lesson(self.root, n, roadmap.get(n))
            if lesson:
                self.lessons[n] = lesson

    def get(self, number: int) -> LessonContent:
        lesson = self.lessons.get(int(number))
        if not lesson:
            raise KeyError(f"Нет занятия {number}")
        return lesson

    def all(self) -> list[LessonContent]:
        return [self.lessons[n] for n in sorted(self.lessons)]

    @property
    def count(self) -> int:
        return len(self.lessons)


@lru_cache(maxsize=1)
def get_bank() -> ContentBank:
    return ContentBank()


def reload_bank() -> ContentBank:
    get_bank.cache_clear()
    return get_bank()
