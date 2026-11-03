"""Баланс в белорусских рублях и списание стоимости запросов к модели.

Стоимость запроса и ответа оценивается по ценам модели, затем умножается на 20
(наценка 1900%). Если на балансе меньше этой оценки, запрос к модели не отправляется.
После ответа списывается фактическая стоимость (по счёту провайдера или по токенам)
с той же наценкой.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from threading import RLock
from typing import Any

from .config import Config
from .models import BalanceLedger, User, db

log = logging.getLogger(__name__)

MONEY = Decimal("0.0001")
_MARKUP = Config.AI_PRICE_MARKUP
_LOCK = RLock()
_RATE_CACHE: dict[str, Any] = {"rate": None, "at": 0.0}
_RATE_TTL = 6 * 3600
_NBRB_URL = "https://api.nbrb.by/exrates/rates/USD?parammode=2"


class ModelUsage:
    """Сумма токенов и долларов по одному или нескольким вызовам модели."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd: Decimal | None = None
        self.calls = 0
        self.priced_calls = 0

    def add_call(self, prompt: int, completion: int, cost: Any) -> None:
        self.prompt_tokens += max(0, int(prompt or 0))
        self.completion_tokens += max(0, int(completion or 0))
        self.calls += 1
        if cost is None:
            return
        try:
            amount = Decimal(str(cost))
        except InvalidOperation:
            return
        self.cost_usd = (self.cost_usd or Decimal(0)) + amount
        self.priced_calls += 1

    def add(self, other: ModelUsage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls += other.calls
        self.priced_calls += other.priced_calls
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or Decimal(0)) + other.cost_usd

    def billed_usd(self) -> Decimal:
        if self.calls and self.priced_calls == self.calls and self.cost_usd is not None:
            return self.cost_usd
        return token_usd(self.prompt_tokens, self.completion_tokens)


def money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).replace(",", "."))
        except InvalidOperation:
            return Decimal("0.0000")
    return amount.quantize(MONEY, rounding=ROUND_HALF_UP)


def parse_byn(raw: str) -> Decimal | None:
    text = (raw or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0 or value > Decimal("1000000"):
        return None
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def format_byn(value: Any) -> str:
    amount = money(value)
    coarse = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount == coarse:
        text = f"{coarse:.2f}"
    else:
        text = f"{amount:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + " Br"


def format_byn_delta(value: Any) -> str:
    amount = money(value)
    body = format_byn(abs(amount))
    if amount > 0:
        return "+" + body
    if amount < 0:
        return "−" + body
    return body


def byn_input(value: Any) -> str:
    text = format(money(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if "." not in text:
        return text + ".00"
    frac = text.split(".", 1)[1]
    if len(frac) == 1:
        return text + "0"
    return text


def insufficient_message() -> str:
    return "Недостаточно средств на балансе."


def estimate_text_tokens(text: str) -> int:
    n = len(text or "")
    if n <= 0:
        return 0
    return max(1, math.ceil(n / 2))


def estimate_image_tokens(path: Path | None) -> int:
    if path is None:
        return 1100
    try:
        from PIL import Image

        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        return 1100
    return _vision_tokens(width, height)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 4
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    total += estimate_text_tokens(str(part.get("text") or ""))
                elif part.get("type") == "image_url":
                    total += 1100
    return max(1, total)


def token_usd(prompt_tokens: int, completion_tokens: int) -> Decimal:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    long = prompt > int(Config.AI_LONG_CONTEXT_TOKENS)
    pin = Config.AI_INPUT_USD_PER_MTOK_LONG if long else Config.AI_INPUT_USD_PER_MTOK
    pout = Config.AI_OUTPUT_USD_PER_MTOK_LONG if long else Config.AI_OUTPUT_USD_PER_MTOK
    return (Decimal(prompt) * pin + Decimal(completion) * pout) / Decimal(1_000_000)


def usd_byn_rate() -> Decimal:
    override = (os.getenv("USD_BYN_RATE") or "").strip()
    if override:
        try:
            rate = Decimal(override)
            if rate > 0:
                return rate
        except InvalidOperation:
            pass
    now = time.time()
    cached = _RATE_CACHE.get("rate")
    if isinstance(cached, Decimal) and now - float(_RATE_CACHE.get("at") or 0) < _RATE_TTL:
        return cached
    fetched = _fetch_nbrb_rate()
    if fetched is None:
        fallback_raw = (os.getenv("USD_BYN_FALLBACK") or "3.3").strip() or "3.3"
        try:
            fetched = Decimal(fallback_raw)
        except InvalidOperation:
            fetched = Decimal("3.3")
        log.warning("Курс USD/BYN недоступен, используется запасной %s", fetched)
    _RATE_CACHE["rate"] = fetched
    _RATE_CACHE["at"] = now
    return fetched


def to_byn(usd: Decimal) -> Decimal:
    amount = Decimal(usd) * usd_byn_rate() * _MARKUP
    if amount < 0:
        amount = Decimal(0)
    return amount.quantize(MONEY, rounding=ROUND_HALF_UP)


def quote_tokens(prompt_tokens: int, completion_tokens: int) -> Decimal:
    return to_byn(token_usd(prompt_tokens, completion_tokens))


def usage_byn(usage: ModelUsage) -> Decimal:
    if usage.calls <= 0:
        return Decimal("0.0000")
    return to_byn(usage.billed_usd())


def usage_from_response(resp: Any) -> ModelUsage:
    usage = ModelUsage()
    raw = getattr(resp, "usage", None)
    if raw is None:
        return usage
    prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
    completion = int(getattr(raw, "completion_tokens", 0) or 0)
    usage.add_call(prompt, completion, _usage_cost(raw))
    return usage


def absorb_usage(bucket: ModelUsage, exc: BaseException) -> None:
    extra = getattr(exc, "usage", None)
    if isinstance(extra, ModelUsage):
        bucket.add(extra)


def affordable(user_id: int, required: Decimal) -> tuple[bool, Decimal]:
    user = db.session.get(User, user_id)
    balance = money(user.balance_byn if user is not None else 0)
    need = money(required)
    if balance <= 0:
        return False, balance
    return balance >= need, balance


def hold(user_id: int, amount: Decimal, kind: str, ref: str) -> int | None:
    """Резервирует оценку стоимости. None — денег меньше, чем нужно для запроса."""
    amount = money(amount)
    with _LOCK:
        existing = (
            BalanceLedger.query.filter_by(user_id=user_id, ref=ref, status="held")
            .order_by(BalanceLedger.id.desc())
            .first()
        )
        if existing is not None:
            return existing.id
        user = db.session.query(User).filter_by(id=user_id).with_for_update().populate_existing().one_or_none()
        if user is None:
            db.session.rollback()
            return None
        balance = money(user.balance_byn)
        if balance <= 0 or balance < amount:
            db.session.rollback()
            return None
        user.balance_byn = balance - amount
        row = BalanceLedger(
            user_id=user_id,
            amount_byn=-amount,
            balance_after=user.balance_byn,
            status="held",
            kind=kind,
            ref=ref,
            details={"held": str(amount)},
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def settle(
    ledger_id: int,
    actual: Decimal,
    details: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> Decimal:
    """Заменяет резерв фактической стоимостью и возвращает списанную сумму."""
    raw_actual = money(actual)
    with _LOCK:
        row = db.session.get(BalanceLedger, ledger_id)
        if row is None or row.status != "held":
            return Decimal("0.0000")
        user = db.session.query(User).filter_by(id=row.user_id).with_for_update().populate_existing().one()
        held = money(-money(row.amount_byn))
        balance = money(user.balance_byn)
        extra = raw_actual - held
        if extra > balance:
            extra = balance
        charged = held + extra
        user.balance_byn = balance - extra
        row.amount_byn = -charged
        row.balance_after = user.balance_byn
        row.status = "settled"
        stored = dict(details or {})
        stored["held"] = str(held)
        stored["actual"] = str(raw_actual)
        stored["charged"] = str(charged)
        row.details = stored
        if commit:
            db.session.commit()
        return charged


def release(ledger_id: int, *, commit: bool = True) -> None:
    with _LOCK:
        row = db.session.get(BalanceLedger, ledger_id)
        if row is None or row.status != "held":
            return
        user = db.session.query(User).filter_by(id=row.user_id).with_for_update().populate_existing().one()
        refund = money(-money(row.amount_byn))
        user.balance_byn = money(user.balance_byn) + refund
        row.amount_byn = Decimal("0.0000")
        row.balance_after = user.balance_byn
        row.status = "released"
        if commit:
            db.session.commit()


def release_ref(ref: str, *, commit: bool = True) -> None:
    row = (
        BalanceLedger.query.filter_by(ref=ref, status="held")
        .order_by(BalanceLedger.id.desc())
        .first()
    )
    if row is not None:
        release(row.id, commit=commit)


def charge_for_response(
    user_id: int,
    amount: Decimal,
    kind: str,
    ref: str,
    details: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> Decimal:
    """Списывает фактическую стоимость запроса и ответа в момент получения ответа.

    Повтор с тем же ref не списывает сумму второй раз. Баланс не уходит ниже нуля.
    Старый резерв по этому ref сначала возвращается.
    """
    amount = money(amount)
    with _LOCK:
        already = (
            BalanceLedger.query.filter_by(user_id=user_id, ref=ref, status="settled", kind=kind)
            .order_by(BalanceLedger.id.desc())
            .first()
        )
        if already is not None:
            return money(-money(already.amount_byn))
        release_ref(ref, commit=False)
        user = db.session.query(User).filter_by(id=user_id).with_for_update().populate_existing().one_or_none()
        if user is None:
            if commit:
                db.session.rollback()
            return Decimal("0.0000")
        balance = money(user.balance_byn)
        charged = amount if amount <= balance else balance
        if charged < 0:
            charged = Decimal("0.0000")
        user.balance_byn = balance - charged
        stored = dict(details or {})
        stored["actual"] = str(amount)
        stored["charged"] = str(charged)
        db.session.add(
            BalanceLedger(
                user_id=user_id,
                amount_byn=-charged,
                balance_after=user.balance_byn,
                status="settled",
                kind=kind,
                ref=ref,
                details=stored,
            )
        )
        if commit:
            db.session.commit()
        return charged


def commit_with_balance(user: User, new_value: Decimal, actor_id: int | None) -> None:
    """Фиксирует правки пользователя и ставит баланс в указанную сумму."""
    new = money(new_value)
    with _LOCK:
        if user.id is not None:
            current = db.session.query(User.balance_byn).filter_by(id=user.id).with_for_update().scalar()
            user.balance_byn = money(current)
        record_balance_change(user, new, actor_id)
        db.session.commit()


def record_balance_change(user: User, new_value: Decimal, actor_id: int | None) -> None:
    old = money(user.balance_byn)
    new = money(new_value)
    user.balance_byn = new
    if old == new or user.id is None:
        return
    db.session.add(
        BalanceLedger(
            user_id=user.id,
            amount_byn=new - old,
            balance_after=new,
            status="settled",
            kind="admin",
            ref=f"admin:{actor_id or 0}",
            details={"actor_id": actor_id},
        )
    )


def _vision_tokens(width: int, height: int) -> int:
    w = float(width or 0)
    h = float(height or 0)
    if w <= 0 or h <= 0:
        return 1100
    long_side = max(w, h)
    if long_side > 2048:
        scale = 2048 / long_side
        w *= scale
        h *= scale
    short_side = min(w, h)
    if short_side > 768:
        scale = 768 / short_side
        w *= scale
        h *= scale
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return 85 + 170 * max(1, tiles)


def _usage_cost(raw: Any) -> Any:
    cost = getattr(raw, "cost", None)
    if cost is not None:
        return cost
    extra = getattr(raw, "model_extra", None) or getattr(raw, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        return extra.get("cost")
    return None


def _fetch_nbrb_rate() -> Decimal | None:
    try:
        request = urllib.request.Request(_NBRB_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        scale = Decimal(str(data.get("Cur_Scale") or 1))
        rate = Decimal(str(data["Cur_OfficialRate"])) / scale
        if rate <= 0:
            return None
        return rate
    except Exception:
        log.warning("Не удалось получить курс USD от НБРБ", exc_info=True)
        return None
