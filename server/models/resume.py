"""简历表 ORM。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), default="pasted.txt")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    # 结构化候选人画像 ResumeProfile
    profile_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 画像缓存：内容哈希 / 解析时间 / 画像版本（内容不变则跳过重新解析）
    content_hash: Mapped[str] = mapped_column(String(32), default="")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
