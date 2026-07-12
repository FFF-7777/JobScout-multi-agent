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
    _migrate_agent_runs_eta()
    _migrate_jobs_analyze_mode()
    _migrate_jobs_ocr_fields()
    _migrate_resume_cache()
    _migrate_agent_runs_progress_fields()
    _migrate_match_results_cache()


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


def _migrate_agent_runs_eta() -> None:
    """为 agent_runs 补齐 ETA / current_item 列（增量进度可视化用），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    needed = {
        "eta_seconds": "ALTER TABLE agent_runs ADD COLUMN eta_seconds INTEGER DEFAULT 0",
        "current_item": "ALTER TABLE agent_runs ADD COLUMN current_item VARCHAR(255) DEFAULT ''",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_jobs_analyze_mode() -> None:
    """为 jobs 补 analyze_mode 列（summary / full），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("jobs")}
    if "analyze_mode" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN analyze_mode VARCHAR(16) DEFAULT 'summary'")
            )


def _migrate_jobs_ocr_fields() -> None:
    """为 jobs 补 OCR 三层数据 / 解析状态列，幂等。

    cleaned_jd_text（清洗后正文）/ jd_summary（列表预览摘要）/
    parse_status（pending/parsing/success/failed）/ parse_error（失败原因）。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("jobs")}
    needed = {
        "cleaned_jd_text": "ALTER TABLE jobs ADD COLUMN cleaned_jd_text TEXT DEFAULT ''",
        "jd_summary": "ALTER TABLE jobs ADD COLUMN jd_summary VARCHAR(512) DEFAULT ''",
        "parse_status": "ALTER TABLE jobs ADD COLUMN parse_status VARCHAR(16) DEFAULT 'pending'",
        "parse_error": "ALTER TABLE jobs ADD COLUMN parse_error VARCHAR(512) DEFAULT ''",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_resume_cache() -> None:
    """为 resumes 补画像缓存列（content_hash / parsed_at / profile_version），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "resumes" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("resumes")}
    needed = {
        "content_hash": "ALTER TABLE resumes ADD COLUMN content_hash VARCHAR(32) DEFAULT ''",
        "parsed_at": "ALTER TABLE resumes ADD COLUMN parsed_at TIMESTAMP",
        "profile_version": "ALTER TABLE resumes ADD COLUMN profile_version INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_agent_runs_progress_fields() -> None:
    """为 agent_runs 补进度/并发可视化列（ETA 范围、计数、在途岗位），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    needed = {
        "eta_low": "ALTER TABLE agent_runs ADD COLUMN eta_low INTEGER DEFAULT 0",
        "eta_high": "ALTER TABLE agent_runs ADD COLUMN eta_high INTEGER DEFAULT 0",
        "total_items": "ALTER TABLE agent_runs ADD COLUMN total_items INTEGER DEFAULT 0",
        "completed_items": "ALTER TABLE agent_runs ADD COLUMN completed_items INTEGER DEFAULT 0",
        "failed_items": "ALTER TABLE agent_runs ADD COLUMN failed_items INTEGER DEFAULT 0",
        "in_flight_items": "ALTER TABLE agent_runs ADD COLUMN in_flight_items JSON",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_match_results_cache() -> None:
    """为 match_results 补缓存键/命中列，幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "match_results" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("match_results")}
    needed = {
        "cache_key": "ALTER TABLE match_results ADD COLUMN cache_key VARCHAR(64)",
        "cache_hit": "ALTER TABLE match_results ADD COLUMN cache_hit BOOLEAN DEFAULT 0",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))
