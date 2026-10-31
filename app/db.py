"""SQLAlchemy engine, session and models. Same models on Postgres and SQLite.

Mirrors the DDL in the TRD; JSON columns are portable JSON instead of JSONB,
and fetch_ids is a JSON list instead of BIGINT[] so SQLite works locally.
Two extra tables: traces (per-node agent traces) and app_errors (shown on /ops).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


ID = BigInteger().with_variant(Integer, "sqlite")


class Farm(Base):
    __tablename__ = "farms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mobile: Mapped[str | None] = mapped_column(String(20), unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    village: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    place_name: Mapped[str | None] = mapped_column(Text)  # geocoder display name
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    crop: Mapped[str | None] = mapped_column(String(20))  # paddy_aman | potato
    area_bigha: Mapped[float | None] = mapped_column(Float)
    stage: Mapped[str | None] = mapped_column(String(10))  # growing|ready|cut|sold|to_sow
    cut_date: Mapped[date | None] = mapped_column(Date)
    insured: Mapped[bool | None] = mapped_column(Boolean)
    language: Mapped[str] = mapped_column(String(5), default="bn")
    address_as: Mapped[str | None] = mapped_column(Text)  # only what the farmer chose
    profile_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # farmer | sathi | system
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(String(5))
    timings: Mapped[dict | None] = mapped_column(JSON)
    decision_ids: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceFetch(Base):
    __tablename__ = "source_fetches"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30))
    loc_key: Mapped[str | None] = mapped_column(String(30))
    url: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict | None] = mapped_column(JSON)
    ok: Mapped[bool] = mapped_column(Boolean)
    raw: Mapped[dict | list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_fetch_loc", "loc_key", "source", "fetched_at"),)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"))
    module: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[dict] = mapped_column(JSON)
    reason_en: Mapped[str] = mapped_column(Text)
    reason_bn: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(String(10))
    fetch_ids: Mapped[list] = mapped_column(JSON, default=list)
    rule_sources: Mapped[list] = mapped_column(JSON, default=list)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    hash: Mapped[str] = mapped_column(String(64))
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10), default="monitor")  # monitor | turn
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_decisions_farm_module", "farm_id", "module", "created_at"),)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"))
    tier: Mapped[str] = mapped_column(String(10))
    text_bn: Mapped[str] = mapped_column(Text)
    text_en: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    farm_id: Mapped[str] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id", ondelete="SET NULL"))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rain_mm: Mapped[float | None] = mapped_column(Float)
    checklist: Mapped[list] = mapped_column(JSON)
    report_text_bn: Mapped[str] = mapped_column(Text)
    report_text_en: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Trace(Base):
    __tablename__ = "traces"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    graph: Mapped[str] = mapped_column(String(15))  # monitor | conversation
    node: Mapped[str] = mapped_column(String(30))
    inputs_hash: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppError(Base):
    __tablename__ = "app_errors"
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    where: Mapped[str] = mapped_column(String(60))
    error: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _make_engine():
    url = settings().database_url
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # enforce ON DELETE CASCADE in SQLite
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log_error(where: str, err: Exception | str) -> None:
    """Record an error for the operator view. Never raises."""
    try:
        with session_scope() as s:
            s.add(AppError(where=where[:60], error=str(err)[:2000]))
    except Exception:
        pass
