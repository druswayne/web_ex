"""Проверка логина, почты и пароля при регистрации и в кабинете."""

from __future__ import annotations

import re

MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_EMAIL = 254

_USERNAME_RE = re.compile(r"\A[\w][\w.-]{1,30}[\w]\Z", re.UNICODE)
_EMAIL_RE = re.compile(
    r"\A[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\Z"
)
_COMMON_PASSWORDS = {
    "12345678",
    "123456789",
    "1234567890",
    "0123456789",
    "11111111",
    "00000000",
    "password",
    "password1",
    "password123",
    "qwerty123",
    "qwertyui",
    "qwertyuiop",
    "1q2w3e4r",
    "abc12345",
    "iloveyou",
    "admin123",
    "letmein1",
    "пароль123",
    "йцукен123",
}


def normalize_email(value: str) -> str:
    return (value or "").strip().casefold()


def username_error(value: str) -> str | None:
    name = (value or "").strip()
    if not _USERNAME_RE.match(name) or ".." in name:
        return (
            "Логин — от 3 до 32 символов: буквы, цифры, точка, дефис или подчёркивание. "
            "Без пробелов, с буквы или цифры в начале и в конце."
        )
    return None


def email_error(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return "Укажите эл. почту."
    if len(text) > MAX_EMAIL or _EMAIL_RE.match(text) is None:
        return "Почта выглядит неверно. Проверьте адрес."
    return None


def password_error(password: str, *, username: str = "", email: str = "") -> str | None:
    if not password:
        return "Введите пароль."
    if len(password) < MIN_PASSWORD:
        return f"Пароль — не короче {MIN_PASSWORD} символов."
    if len(password) > MAX_PASSWORD:
        return f"Пароль — не длиннее {MAX_PASSWORD} символов."
    if len(set(password)) == 1:
        return "Пароль не может состоять из одного повторяющегося символа."
    folded = password.casefold()
    banned = {username.strip().casefold(), normalize_email(email)}
    local = normalize_email(email).split("@", 1)[0]
    if local:
        banned.add(local)
    banned.discard("")
    if folded in banned:
        return "Пароль не должен совпадать с логином или почтой."
    if folded in _COMMON_PASSWORDS:
        return "Этот пароль слишком распространённый. Выберите другой."
    return None
