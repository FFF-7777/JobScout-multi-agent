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
    """启动时初始化数据库。

    分两种情况：
    1. 全新 DB（或未启用 Alembic 的旧库）：Base.metadata.create_all + 15 个幂等 _migrate_*
       → alembic stamp head（标记为当前版本）
    2. 已启用 Alembic 的库：alembic upgrade head（仅应用增量迁移）

    导入所有模型以注册到 Base.metadata。
    """
    import models  # noqa: F401  确保模型被加载
    from alembic.command import stamp, upgrade
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import inspect, text

    _enable_wal()

    inspector = inspect(engine)
    alembic_cfg = AlembicConfig("alembic.ini")
    # 避免 alembic 从 ini 文件读取数据库 URL（由 env.py 接管）
    alembic_cfg.attributes["configure_logger"] = False

    if "alembic_version" not in inspector.get_table_names():
        # ── 旧库 / 全新库：先走原有建表+迁移，再打基线标记 ──
        Base.metadata.create_all(bind=engine)
        _migrate_agent_runs_progress()
        _migrate_agent_runs_timestamps()
        _migrate_agent_runs_eta()
        _migrate_jobs_analyze_mode()
        _migrate_jobs_ocr_fields()
        _migrate_resume_cache()
        _migrate_ocr_audit_fields()
        _migrate_agent_runs_progress_fields()
        _migrate_match_results_cache()
        _migrate_match_results_status()
        _migrate_match_results_deep()
        _migrate_agent_item_runs()
        _migrate_agent_item_run_observability()
        _migrate_agent_item_runs_unique()
        _migrate_job_parse_tasks()
        _migrate_job_reports()
        _migrate_unique_constraints()
        stamp(alembic_cfg, "head")
    else:
        # ── 已启用 Alembic：增量升级 ──
        upgrade(alembic_cfg, "head")


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


def _migrate_ocr_audit_fields() -> None:
    """为岗位和简历补 OCR 降级链路审计 JSON。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table in ("jobs", "resumes"):
        if table not in tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "ocr_metadata" not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN ocr_metadata JSON"))


def _migrate_agent_item_run_observability() -> None:
    """为单岗位执行记录补实时阶段与研究元数据。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_item_runs" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("agent_item_runs")}
    needed = {
        "phase": "ALTER TABLE agent_item_runs ADD COLUMN phase VARCHAR(32) DEFAULT ''",
        "metadata_json": "ALTER TABLE agent_item_runs ADD COLUMN metadata_json JSON",
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


def _migrate_match_results_status() -> None:
    """为 match_results 补档位 / 状态 / 报告缓存键列（P1#7 / P1#8 / P2#14），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "match_results" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("match_results")}
    needed = {
        "match_mode": "ALTER TABLE match_results ADD COLUMN match_mode VARCHAR(8) DEFAULT 'deep'",
        "status": "ALTER TABLE match_results ADD COLUMN status VARCHAR(16) DEFAULT 'success'",
        "error_message": "ALTER TABLE match_results ADD COLUMN error_message TEXT DEFAULT ''",
        "report_cache_key": "ALTER TABLE match_results ADD COLUMN report_cache_key VARCHAR(64)",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_match_results_deep() -> None:
    """为 match_results 补两档分数 / 深度失败标记（P1#11），幂等。"""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "match_results" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("match_results")}
    needed = {
        "quick_score": "ALTER TABLE match_results ADD COLUMN quick_score FLOAT DEFAULT 0.0",
        "deep_score": "ALTER TABLE match_results ADD COLUMN deep_score FLOAT",
        "partial_success": "ALTER TABLE match_results ADD COLUMN partial_success BOOLEAN DEFAULT 0",
        "deep_error_message": "ALTER TABLE match_results ADD COLUMN deep_error_message TEXT DEFAULT ''",
    }
    with engine.begin() as conn:
        for col, ddl in needed.items():
            if col not in columns:
                conn.execute(text(ddl))


def _migrate_agent_item_runs() -> None:
    """新建 agent_item_runs 表（P2#10 单条执行记录），幂等。"""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "agent_item_runs" in inspector.get_table_names():
        return
    Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["agent_item_runs"]])


def _migrate_job_parse_tasks() -> None:
    """新建 job_parse_tasks 表（P1#9 后台解析队列），幂等。"""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "job_parse_tasks" in inspector.get_table_names():
        return
    Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["job_parse_tasks"]])


def _migrate_agent_item_runs_unique() -> None:
    """为 agent_item_runs 补唯一索引（P1#10），防止同(任务+Agent+岗位+档位)重复行。"""
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_item_runs_unique "
                    "ON agent_item_runs(task_id, agent_name, item_id, tier)"
                )
            )
    except Exception:
        pass  # 索引已存在或表不存在时不报错


def _migrate_job_reports() -> None:
    """新建 job_reports 表（P1#12 报告独立存储），并复制已有报告，幂等。"""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "job_reports" not in inspector.get_table_names():
        Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["job_reports"]])
    # 迁移已有报告：从 match_results.detail_json["report"] 复制到 job_reports
    _copy_reports_to_job_reports()


def _copy_reports_to_job_reports() -> None:
    """把 match_results.detail_json["report"] 中已有的报告写入 job_reports（幂等）。"""
    from models import JobReport, MatchResult

    s = SessionLocal()
    try:
        count = 0
        rows = s.query(MatchResult).filter(
            MatchResult.detail_json.isnot(None),
            MatchResult.report_cache_key.isnot(None),
        ).all()
        for row in rows:
            report = (row.detail_json or {}).get("report")
            if not report:
                continue
            mode = report.get("mode", "standard") if isinstance(report, dict) else "standard"
            existing = s.query(JobReport).filter(
                JobReport.match_result_id == row.id,
                JobReport.mode == mode,
            ).first()
            if existing:
                continue
            jr = JobReport(
                match_result_id=row.id,
                mode=mode,
                report_json=report,
                cache_key=row.report_cache_key,
            )
            s.add(jr)
            count += 1
            if count % 50 == 0:
                s.commit()
        if count:
            s.commit()
    finally:
        s.close()


def _migrate_unique_constraints() -> None:
    """为 AgentRun / MatchResult / JobAnalysis 添加唯一约束索引（P2），幂等。

    流程：先清理重复行（保留最小 id），再 CREATE UNIQUE INDEX IF NOT EXISTS。
    SQLite 的 NULL 在 UNIQUE 约束中视为互异，故 task_id=NULL 的 MatchResult 不受影响。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    def _dedup_and_create(table: str, cols: str, index_name: str) -> None:
        if table not in inspector.get_table_names():
            return
        with engine.begin() as conn:
            # 清理重复行：保留每组 (cols) 最小 id 的那行，删除其余
            conn.execute(text(
                f"DELETE FROM {table} WHERE id NOT IN "
                f"(SELECT MIN(id) FROM {table} GROUP BY {cols})"
            ))
            conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                f"ON {table}({cols})"
            ))

    _dedup_and_create("agent_runs", "task_id, agent_name", "uq_agent_runs_task_agent")
    _dedup_and_create("job_analysis", "job_id", "uq_job_analysis_job_id")

    # match_results: task_id 可为 NULL，SQLite 中 NULL!=NULL，故仅对 task_id NOT NULL 的行约束
    if "match_results" in inspector.get_table_names():
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM match_results WHERE id NOT IN "
                "(SELECT MIN(id) FROM match_results "
                "WHERE task_id IS NOT NULL GROUP BY task_id, job_id) "
                "AND task_id IS NOT NULL"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_match_results_task_job "
                "ON match_results(task_id, job_id) WHERE task_id IS NOT NULL"
            ))
