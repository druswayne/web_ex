from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import DeclarativeBase
from werkzeug.security import check_password_hash, generate_password_hash


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=True, index=True)
    role = db.Column(db.String(20), nullable=False, default="student", index=True)
    level_band = db.Column(db.String(16), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    lesson_access = db.relationship("LessonAccess", backref="user", cascade="all, delete-orphan")
    lesson_progress = db.relationship("LessonProgress", backref="user", cascade="all, delete-orphan")
    test_attempts = db.relationship("TestAttempt", backref="user", cascade="all, delete-orphan")
    practice_assignments = db.relationship(
        "PracticeAssignment", backref="user", cascade="all, delete-orphan"
    )
    activities = db.relationship(
        "ActivityLog",
        backref="subject",
        foreign_keys="ActivityLog.user_id",
        cascade="all, delete-orphan",
    )
    achievements = db.relationship("UserAchievement", backref="user", cascade="all, delete-orphan")
    theory_chats = db.relationship(
        "TheoryChatMessage", backref="user", cascade="all, delete-orphan"
    )
    balance_byn = db.Column(
        db.Numeric(12, 4), nullable=False, default=Decimal("2"), server_default="2"
    )
    balance_entries = db.relationship(
        "BalanceLedger", backref="user", cascade="all, delete-orphan"
    )

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256")

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @staticmethod
    def normalize_username(value: str) -> str:
        return (value or "").strip().casefold()

    @classmethod
    def find_by_username(cls, value: str) -> User | None:
        key = cls.normalize_username(value)
        if not key:
            return None
        matches = cls.query.filter(func.lower(cls.username) == key).all()
        if matches:
            return matches[0]
        # SQLite LOWER() не трогает кириллицу — добираем в Python.
        return next((u for u in cls.query.all() if u.username.casefold() == key), None)

    @classmethod
    def username_taken(cls, value: str, exclude_id: int | None = None) -> bool:
        user = cls.find_by_username(value)
        return user is not None and user.id != exclude_id

    @staticmethod
    def normalize_email(value: str) -> str:
        return (value or "").strip().casefold()

    @classmethod
    def find_by_email(cls, value: str) -> User | None:
        key = cls.normalize_email(value)
        if not key:
            return None
        return cls.query.filter(func.lower(cls.email) == key).first()

    @classmethod
    def email_taken(cls, value: str, exclude_id: int | None = None) -> bool:
        user = cls.find_by_email(value)
        return user is not None and user.id != exclude_id

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_student(self) -> bool:
        return self.role == "student"


class LessonAccess(db.Model):
    __tablename__ = "lesson_access"
    __table_args__ = (db.UniqueConstraint("user_id", "lesson_number", name="uq_access_user_lesson"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    lesson_number = db.Column(db.Integer, nullable=False, index=True)
    unlocked = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(20), nullable=False, default="progress")
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class LessonProgress(db.Model):
    __tablename__ = "lesson_progress"
    __table_args__ = (db.UniqueConstraint("user_id", "lesson_number", name="uq_progress_user_lesson"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    lesson_number = db.Column(db.Integer, nullable=False, index=True)
    theory_scroll_pct = db.Column(db.Integer, nullable=False, default=0)
    theory_completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    theory_read_seconds = db.Column(db.Integer, nullable=False, default=0)
    theory_video_completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    test_best_score = db.Column(db.Float, nullable=True)
    test_passed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    practice_correct = db.Column(db.Integer, nullable=False, default=0)
    practice_total = db.Column(db.Integer, nullable=False, default=0)
    practice_passed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class TestAttempt(db.Model):
    __tablename__ = "test_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    lesson_number = db.Column(db.Integer, nullable=False, index=True)
    attempt_no = db.Column(db.Integer, nullable=False)
    answers = db.Column(db.JSON, nullable=False)
    details = db.Column(db.JSON, nullable=False)
    correct_count = db.Column(db.Integer, nullable=False)
    total_count = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False)
    passed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class PracticeAssignment(db.Model):
    __tablename__ = "practice_assignments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    lesson_number = db.Column(db.Integer, nullable=False, index=True)
    slot = db.Column(db.Integer, nullable=False)
    task_code = db.Column(db.String(16), nullable=False)
    level_band = db.Column(db.String(16), nullable=False)
    title = db.Column(db.Text, nullable=False, default="")
    task_md = db.Column(db.Text, nullable=False)
    answer_text = db.Column(db.Text, nullable=False, default="")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    replaced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    attempts = db.relationship(
        "PracticeAttempt",
        backref="assignment",
        cascade="all, delete-orphan",
        order_by="PracticeAttempt.created_at",
    )


class PracticeAttempt(db.Model):
    __tablename__ = "practice_attempts"

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(
        db.Integer, db.ForeignKey("practice_assignments.id"), nullable=False, index=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    attempt_no = db.Column(db.Integer, nullable=False)
    photo_filename = db.Column(db.String(260), nullable=False)
    processed_filename = db.Column(db.String(260), nullable=True)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    ai_verdict = db.Column(db.String(32), nullable=False, default="error")
    ai_feedback = db.Column(db.Text, nullable=False, default="")
    ai_raw = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    lesson_number = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(40), nullable=False, index=True)
    details = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class UserAchievement(db.Model):
    __tablename__ = "user_achievements"
    __table_args__ = (db.UniqueConstraint("user_id", "code", name="uq_user_achievement"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255), nullable=False, default="")
    earned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class BalanceLedger(db.Model):
    """Движение баланса: резерв на запрос, фактическое списание или правка администратора."""

    __tablename__ = "balance_ledger"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    amount_byn = db.Column(db.Numeric(12, 4), nullable=False)
    balance_after = db.Column(db.Numeric(12, 4), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="settled", index=True)
    kind = db.Column(db.String(16), nullable=False)
    ref = db.Column(db.String(40), nullable=False, default="", index=True)
    details = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


SUPPORT_KINDS = {
    "question": "Вопрос",
    "complaint": "Жалоба",
    "other": "Другое",
}


class SupportMessage(db.Model):
    """Вопрос, жалоба или другое обращение ученика к администратору."""

    __tablename__ = "support_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    kind = db.Column(db.String(20), nullable=False, default="question", index=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)

    author = db.relationship("User", foreign_keys=[user_id])


class TheoryChatMessage(db.Model):
    __tablename__ = "theory_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    lesson_number = db.Column(db.Integer, nullable=False, index=True)
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="ready", server_default="ready")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


def ensure_schema() -> None:
    """Добавляет колонки, которых нет в уже существующей базе."""
    inspector = inspect(db.engine)
    names = set(inspector.get_table_names())
    alters: list[str] = []
    if "lesson_progress" in names:
        cols = {c["name"] for c in inspector.get_columns("lesson_progress")}
        if "theory_read_seconds" not in cols:
            alters.append(
                "ALTER TABLE lesson_progress "
                "ADD COLUMN theory_read_seconds INTEGER DEFAULT 0"
            )
        if "theory_video_completed_at" not in cols:
            coltype = "DATETIME" if db.engine.dialect.name == "sqlite" else "TIMESTAMP"
            alters.append(
                "ALTER TABLE lesson_progress "
                f"ADD COLUMN theory_video_completed_at {coltype}"
            )
    if "users" in names:
        cols = {c["name"] for c in inspector.get_columns("users")}
        if "balance_byn" not in cols:
            alters.append(
                "ALTER TABLE users ADD COLUMN balance_byn NUMERIC(12, 4) NOT NULL DEFAULT 0"
            )
        if "email" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN email VARCHAR(254)")
        indexes = inspector.get_indexes("users")
        uniques = inspector.get_unique_constraints("users")
        email_unique = any(
            idx.get("unique") and list(idx.get("column_names") or []) == ["email"] for idx in indexes
        ) or any(list(uc.get("column_names") or []) == ["email"] for uc in uniques)
        if not email_unique:
            alters.append("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
    if "theory_chat_messages" in names:
        cols = {c["name"] for c in inspector.get_columns("theory_chat_messages")}
        if "status" not in cols:
            alters.append(
                "ALTER TABLE theory_chat_messages ADD COLUMN status VARCHAR(16) DEFAULT 'ready'"
            )
    if not alters:
        return
    with db.engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))
