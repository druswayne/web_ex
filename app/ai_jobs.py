"""Общий пул фоновых запросов к модели: проверка фото и тьютор."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable

from .config import Config

_lock = Lock()
_executor: ThreadPoolExecutor | None = None
_queued: set[tuple[str, int]] = set()


def worker_count() -> int:
    return int(Config.AI_CHECK_CONCURRENCY)


def is_queued(kind: str, job_id: int) -> bool:
    """Задание ещё в очереди или уже выполняется в пуле."""
    with _lock:
        return (kind, job_id) in _queued


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=worker_count(),
            thread_name_prefix="ai-job",
        )
    return _executor


def _guard(kind: str, job_id: int, fn: Callable[..., Any], args: tuple[Any, ...]) -> Any:
    try:
        return fn(*args)
    finally:
        with _lock:
            _queued.discard((kind, job_id))


def submit_job(kind: str, job_id: int, fn: Callable[..., Any], *args: Any):
    """Ставит запрос к модели в общую очередь. Одновременно их не больше worker_count()."""
    with _lock:
        _queued.add((kind, job_id))
        pool = _pool()
    try:
        return pool.submit(_guard, kind, job_id, fn, args)
    except Exception:
        with _lock:
            _queued.discard((kind, job_id))
        raise
