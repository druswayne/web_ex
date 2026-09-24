from __future__ import annotations

import math
from typing import Any

from flask import current_app

from .content import get_bank
from .models import (
    ActivityLog,
    LessonAccess,
    LessonProgress,
    PracticeAssignment,
    PracticeAttempt,
    TestAttempt,
    User,
    UserAchievement,
    db,
    utcnow,
)


def log_activity(
    user_id: int,
    action: str,
    *,
    lesson_number: int | None = None,
    details: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    db.session.add(
        ActivityLog(
            user_id=user_id,
            actor_id=actor_id,
            lesson_number=lesson_number,
            action=action,
            details=details or {},
        )
    )


def meets_percent(correct: int, total: int, percent: float) -> bool:
    if total <= 0:
        return False
    need = max(1, int(round(total * percent / 100.0)))
    return correct >= need


def get_or_create_progress(user_id: int, lesson_number: int) -> LessonProgress:
    row = LessonProgress.query.filter_by(user_id=user_id, lesson_number=lesson_number).first()
    if row:
        return row
    row = LessonProgress(user_id=user_id, lesson_number=lesson_number)
    db.session.add(row)
    db.session.flush()
    return row


def get_access(user_id: int, lesson_number: int) -> LessonAccess | None:
    return LessonAccess.query.filter_by(user_id=user_id, lesson_number=lesson_number).first()


def is_unlocked(user: User, lesson_number: int) -> bool:
    if user.is_admin:
        return True
    if lesson_number == 1:
        row = get_access(user.id, 1)
        if row is None:
            return True
        return bool(row.unlocked)
    row = get_access(user.id, lesson_number)
    return bool(row and row.unlocked)


def set_access(
    user_id: int,
    lesson_number: int,
    unlocked: bool,
    source: str = "admin",
    actor_id: int | None = None,
) -> LessonAccess:
    row = get_access(user_id, lesson_number)
    if row is None:
        row = LessonAccess(user_id=user_id, lesson_number=lesson_number)
        db.session.add(row)
    row.unlocked = unlocked
    row.source = source
    row.updated_at = utcnow()
    log_activity(
        user_id,
        "lesson_unlocked" if unlocked else "lesson_locked",
        lesson_number=lesson_number,
        details={"source": source},
        actor_id=actor_id,
    )
    return row


def unlock_next_if_needed(user: User, lesson_number: int) -> None:
    bank = get_bank()
    nxt = lesson_number + 1
    if nxt not in bank.lessons:
        return
    existing = get_access(user.id, nxt)
    if existing and existing.unlocked:
        return
    if existing and not existing.unlocked and existing.source == "admin":
        return
    set_access(user.id, nxt, True, source="progress")


def bootstrap_student(user: User) -> None:
    set_access(user.id, 1, True, source="progress")


def theory_done(progress: LessonProgress) -> bool:
    return progress.theory_completed_at is not None


def format_duration(seconds: int | None) -> str:
    n = int(seconds or 0)
    if n <= 0:
        return ""
    if n < 60:
        return f"{n} с"
    minutes = int(round(n / 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"{hours} ч {minutes} мин"
    return f"{hours} ч"


_MAX_READ_TICK = 45


def test_done(progress: LessonProgress) -> bool:
    return progress.test_passed_at is not None


def practice_done(progress: LessonProgress) -> bool:
    return progress.practice_passed_at is not None


def mark_theory_progress(
    user: User,
    lesson_number: int,
    pct: int,
    completed: bool,
    seconds: int = 0,
) -> LessonProgress:
    progress = get_or_create_progress(user.id, lesson_number)
    pct = max(0, min(100, int(pct)))
    if pct > progress.theory_scroll_pct:
        progress.theory_scroll_pct = pct
    add = max(0, min(_MAX_READ_TICK, int(seconds or 0)))
    if add:
        progress.theory_read_seconds = int(progress.theory_read_seconds or 0) + add
    if completed and not progress.theory_completed_at:
        progress.theory_completed_at = utcnow()
        progress.theory_scroll_pct = 100
        log_activity(
            user.id,
            "theory_complete",
            lesson_number=lesson_number,
            details={"read_seconds": progress.theory_read_seconds or 0},
        )
        grant_achievements(user)
    progress.updated_at = utcnow()
    return progress


def mark_theory_video(
    user: User,
    lesson_number: int,
    *,
    watched: float,
    duration: float,
) -> LessonProgress:
    progress = get_or_create_progress(user.id, lesson_number)
    if progress.theory_video_completed_at:
        return progress
    dur = float(duration or 0)
    seen = float(watched or 0)
    if dur < 1 or seen < 0:
        return progress
    need = min(dur * 0.92, max(0.0, dur - 0.4))
    if seen + 0.05 < need:
        return progress
    progress.theory_video_completed_at = utcnow()
    progress.updated_at = utcnow()
    log_activity(
        user.id,
        "theory_video_complete",
        lesson_number=lesson_number,
        details={"watched": round(seen, 1), "duration": round(dur, 1)},
    )
    return progress


def record_test_attempt(
    user: User,
    lesson_number: int,
    answers: dict[str, list[str]],
) -> TestAttempt:
    from flask import current_app

    lesson = get_bank().get(lesson_number)
    percent = float(current_app.config.get("TEST_PASS_PERCENT") or 80)
    questions = lesson.test_for(user.level_band)
    details = []
    correct = 0
    for q in questions:
        selected = sorted(answers.get(q.code) or [])
        expected = sorted(q.answers)
        ok = selected == expected and bool(expected)
        if ok:
            correct += 1
        details.append(
            {
                "code": q.code,
                "topic": q.topic,
                "selected": selected,
                "expected": expected,
                "correct": ok,
                "multi": q.multi,
            }
        )
    total = len(questions) or 1
    score = round(100.0 * correct / total, 1)
    passed = meets_percent(correct, total, percent)
    prev = (
        TestAttempt.query.filter_by(user_id=user.id, lesson_number=lesson_number)
        .order_by(TestAttempt.attempt_no.desc())
        .first()
    )
    attempt_no = (prev.attempt_no + 1) if prev else 1
    attempt = TestAttempt(
        user_id=user.id,
        lesson_number=lesson_number,
        attempt_no=attempt_no,
        answers=answers,
        details=details,
        correct_count=correct,
        total_count=total,
        score=score,
        passed=passed,
    )
    db.session.add(attempt)
    progress = get_or_create_progress(user.id, lesson_number)
    if progress.test_best_score is None or score > progress.test_best_score:
        progress.test_best_score = score
    if passed and not progress.test_passed_at:
        progress.test_passed_at = utcnow()
    progress.updated_at = utcnow()
    log_activity(
        user.id,
        "test_submit",
        lesson_number=lesson_number,
        details={"attempt": attempt_no, "score": score, "passed": passed},
    )
    grant_achievements(user)
    return attempt


def active_assignments(user_id: int, lesson_number: int) -> list[PracticeAssignment]:
    rows = (
        PracticeAssignment.query.filter_by(
            user_id=user_id, lesson_number=lesson_number, is_active=True
        )
        .order_by(PracticeAssignment.slot)
        .all()
    )
    return rows


def _copy_task_fields(row: PracticeAssignment, task) -> bool:
    """Подтянуть формулировку из файла урока, не трогая зачёт."""
    changed = False
    if row.title != task.title:
        row.title = task.title
        changed = True
    if row.task_md != task.text_md:
        row.task_md = task.text_md
        changed = True
    if (row.answer_text or "") != (task.answer or ""):
        row.answer_text = task.answer or ""
        changed = True
    if row.slot != task.slot:
        row.slot = task.slot
        changed = True
    return changed


def ensure_assignments(user: User, lesson_number: int) -> list[PracticeAssignment]:
    existing = active_assignments(user.id, lesson_number)
    progress = get_or_create_progress(user.id, lesson_number)
    lesson = get_bank().get(lesson_number)
    band = user.level_band or "1-2"
    tasks = lesson.practice.get(band) or lesson.practice.get("1-2") or []
    by_code = {t.code: t for t in tasks}
    changed = False
    for row in existing:
        task = by_code.get(row.task_code)
        if task and _copy_task_fields(row, task):
            changed = True
    if existing and progress.practice_passed_at:
        if changed:
            db.session.flush()
        return existing
    have = {r.task_code for r in existing}
    added = False
    for task in tasks:
        if task.code in have:
            continue
        db.session.add(
            PracticeAssignment(
                user_id=user.id,
                lesson_number=lesson_number,
                slot=task.slot,
                task_code=task.code,
                level_band=band,
                title=task.title,
                task_md=task.text_md,
                answer_text=task.answer,
                is_active=True,
            )
        )
        added = True
    if added:
        db.session.flush()
        return active_assignments(user.id, lesson_number)
    return existing


def sync_assignments_after_level_change(user: User, new_band: str) -> None:
    """Solved tasks keep their original level; unsolved ones are replaced."""
    bank = get_bank()
    for lesson in bank.all():
        rows = active_assignments(user.id, lesson.number)
        if not rows:
            continue
        progress = get_or_create_progress(user.id, lesson.number)
        if progress.practice_passed_at:
            continue
        kept = {r.task_code: r for r in rows if r.is_correct}
        new_tasks = lesson.practice.get(new_band) or []
        for row in rows:
            if row.is_correct:
                continue
            row.is_active = False
            row.replaced_at = utcnow()
        for task in new_tasks:
            if task.code in kept:
                continue
            db.session.add(
                PracticeAssignment(
                    user_id=user.id,
                    lesson_number=lesson.number,
                    slot=task.slot,
                    task_code=task.code,
                    level_band=new_band,
                    title=task.title,
                    task_md=task.text_md,
                    answer_text=task.answer,
                    is_active=True,
                )
            )
        db.session.flush()
        refresh_practice_progress(user, lesson.number)


def sync_assignments_after_content_change(lesson_number: int, band: str) -> None:
    """Update unsolved practice copies after the teacher edits a level."""
    lesson = get_bank().get(lesson_number)
    tasks = lesson.practice.get(band) or []
    by_code = {t.code: t for t in tasks}
    rows = PracticeAssignment.query.filter_by(
        lesson_number=lesson_number, level_band=band, is_active=True
    ).all()
    by_user: dict[int, list[PracticeAssignment]] = {}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)
    for user_id, user_rows in by_user.items():
        user = db.session.get(User, user_id)
        if user is None:
            continue
        progress = get_or_create_progress(user_id, lesson_number)
        if progress.practice_passed_at:
            for row in user_rows:
                task = by_code.get(row.task_code)
                if task:
                    _copy_task_fields(row, task)
            db.session.flush()
            continue
        active_codes: set[str] = set()
        for row in user_rows:
            task = by_code.get(row.task_code)
            if row.is_correct:
                if task:
                    _copy_task_fields(row, task)
                active_codes.add(row.task_code)
                continue
            task = by_code.get(row.task_code)
            if task is None:
                row.is_active = False
                row.replaced_at = utcnow()
                continue
            row.title = task.title
            row.task_md = task.text_md
            row.answer_text = task.answer
            row.slot = task.slot
            active_codes.add(row.task_code)
        for task in tasks:
            if task.code in active_codes:
                continue
            db.session.add(
                PracticeAssignment(
                    user_id=user_id,
                    lesson_number=lesson_number,
                    slot=task.slot,
                    task_code=task.code,
                    level_band=band,
                    title=task.title,
                    task_md=task.text_md,
                    answer_text=task.answer,
                    is_active=True,
                )
            )
        db.session.flush()
        refresh_practice_progress(user, lesson_number)


def refresh_practice_progress(user: User, lesson_number: int) -> LessonProgress:
    rows = active_assignments(user.id, lesson_number)
    progress = get_or_create_progress(user.id, lesson_number)
    total = len(rows)
    correct = sum(1 for r in rows if r.is_correct)
    progress.practice_total = total
    progress.practice_correct = correct
    percent = float(current_app.config.get("PRACTICE_PASS_PERCENT") or 80)
    if total and meets_percent(correct, total, percent) and not progress.practice_passed_at:
        progress.practice_passed_at = utcnow()
        progress.completed_at = utcnow()
        unlock_next_if_needed(user, lesson_number)
        log_activity(
            user.id,
            "lesson_complete",
            lesson_number=lesson_number,
            details={"correct": correct, "total": total},
        )
    progress.updated_at = utcnow()
    grant_achievements(user)
    return progress


def lesson_state(user: User, lesson_number: int) -> dict[str, Any]:
    progress = get_or_create_progress(user.id, lesson_number)
    unlocked = is_unlocked(user, lesson_number)
    theory = theory_done(progress)
    test = test_done(progress)
    practice = practice_done(progress)
    if not unlocked:
        stage = "locked"
    elif practice:
        stage = "done"
    elif test:
        stage = "practice"
    elif theory:
        stage = "test"
    else:
        stage = "theory"
    return {
        "unlocked": unlocked,
        "stage": stage,
        "progress": progress,
        "theory": theory,
        "test": test,
        "practice": practice,
        "video": bool(progress.theory_video_completed_at),
    }


_SKIP_VERDICTS = ("pending", "error", "unclear")


def lesson_success_map(user_id: int) -> dict[int, dict[str, int]]:
    """Доля удачных попыток теста и практики по занятиям.

    Каждая сдача теста и каждая завершённая проверка фото — одна попытка.
    Не считаем pending/error/unclear: это не успех и не ошибка ученика.
    """
    buckets: dict[int, list[int]] = {}

    def _bump(lesson_number: int, ok: bool) -> None:
        row = buckets.setdefault(lesson_number, [0, 0])
        if ok:
            row[0] += 1
        else:
            row[1] += 1

    for t in TestAttempt.query.filter_by(user_id=user_id).all():
        _bump(t.lesson_number, bool(t.passed))

    practice_rows = (
        db.session.query(PracticeAttempt.is_correct, PracticeAssignment.lesson_number)
        .join(PracticeAssignment, PracticeAttempt.assignment_id == PracticeAssignment.id)
        .filter(
            PracticeAttempt.user_id == user_id,
            PracticeAttempt.ai_verdict.notin_(_SKIP_VERDICTS),
        )
        .all()
    )
    for is_correct, lesson_number in practice_rows:
        _bump(lesson_number, bool(is_correct))

    out: dict[int, dict[str, int]] = {}
    for number, (ok, fail) in buckets.items():
        total = ok + fail
        if not total:
            continue
        out[number] = {
            "ok": ok,
            "fail": fail,
            "total": total,
            "pct": int(round(100 * ok / total)),
        }
    return out


ACHIEVEMENTS = [
    ("first_theory", "Первая теория", "Прочитана теория первого занятия"),
    ("first_test", "Теоретик", "Сдан первый тест на 80% и выше"),
    ("first_practice", "Практик", "Засчитано первое задание практической части"),
    ("perfect_test", "Без ошибок", "Тест сдан на 100%"),
    ("three_lessons", "Разгон", "Завершены 3 занятия"),
    ("half_way", "Половина пути", "Пройдена половина курса"),
    ("finish", "Курс пройден", "Завершены все занятия"),
    ("persistent", "Упорство", "Тест сдан с пятой попытки или позже"),
    ("photo_ten", "Тетрадь в деле", "Отправлено 10 фото решений"),
    ("streak_five", "Серия", "5 занятий подряд завершены"),
]


def grant_achievements(user: User) -> None:
    if not user.is_student:
        return
    have = {a.code for a in UserAchievement.query.filter_by(user_id=user.id).all()}
    bank = get_bank()
    progresses = LessonProgress.query.filter_by(user_id=user.id).all()
    done = [p for p in progresses if p.completed_at]
    tests = TestAttempt.query.filter_by(user_id=user.id).all()
    photos = PracticeAttempt.query.filter_by(user_id=user.id).count()
    correct_practice = PracticeAssignment.query.filter_by(user_id=user.id, is_correct=True).count()

    def add(code: str) -> None:
        if code in have:
            return
        meta = next((x for x in ACHIEVEMENTS if x[0] == code), None)
        if not meta:
            return
        db.session.add(
            UserAchievement(
                user_id=user.id,
                code=code,
                title=meta[1],
                description=meta[2],
            )
        )
        have.add(code)
        log_activity(user.id, "achievement", details={"code": code, "title": meta[1]})

    if any(p.theory_completed_at for p in progresses):
        add("first_theory")
    if any(p.test_passed_at for p in progresses):
        add("first_test")
    if correct_practice:
        add("first_practice")
    if any(t.score >= 99.9 for t in tests):
        add("perfect_test")
    if len(done) >= 3:
        add("three_lessons")
    if bank.count and len(done) >= math.ceil(bank.count / 2):
        add("half_way")
    if bank.count and len(done) >= bank.count:
        add("finish")
    if any(t.passed and t.attempt_no >= 5 for t in tests):
        add("persistent")
    if photos >= 10:
        add("photo_ten")
    nums = sorted(p.lesson_number for p in done)
    streak = 0
    best = 0
    prev = 0
    for n in nums:
        if prev and n == prev + 1:
            streak += 1
        else:
            streak = 1
        best = max(best, streak)
        prev = n
    if best >= 5:
        add("streak_five")
