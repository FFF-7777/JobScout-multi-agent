"""数据库连接与会话管理（SQLAlchemy 2.x）。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import get_settings

settings = get_settings()

# SQLite 需要 check_same_thread=False 以配合 FastAPI 多线程；
# timeout 让并发写入在锁上等待而不是立刻报 "database is locked"
_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用的数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """启动时建表，并对旧表做轻量迁移。导入所有模型以注册到 Base.metadata。"""
    import models  # noqa: F401  确保模型被加载

    Base.metadata.create_all(bind=engine)
    _enable_wal()
    _migrate_agent_runs_progress()
    _migrate_agent_runs_timestamps()


def _enable_wal() -> None:
    """SQLite 启用 WAL 日志模式，降低并发写入的锁冲突概率。"""
    if not settings.database_url.startswith("sqlite"):
        return
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    except Exception:
        # WAL 不可用时退回默认模式，不影响建表/迁移
        pass


def _migrate_agent_runs_progress() -> None:
    """如果 agent_runs 表缺少 progress 列，则自动添加（SQLite 兼容）。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    if "progress" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs ADD COLUMN progress INTEGER DEFAULT 0"))


def _migrate_agent_runs_timestamps() -> None:
    """补齐 agent_runs 可能缺失的时间列（started_at/finished_at），保持幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    needed = {
        "started_at": "ALTER TABLE agent_runs ADD COLUMN started_at TIMESTAMP",
        "finished_at": "ALTER TABLE agent_runs ADD COLUMN finished_at TIMESTAMP",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))
