"""domain/reports 的 ORM 模型：一次研究意图发现的报告 + 报告里的条目 +
报告的投递记录。

三张表的关系：一个 `Report` 对应一次 `features/discovery` 的发现运行
（`discovery_run_id`），漏斗里的每个候选落成一个 `ReportItem`（标题筛选
+ AI 摘要分析两个阶段的结果都记在同一行上）；报告定稿后按配置的目的地
各发一条 `Delivery`（`report_id` + `connection_id` 唯一，重发是更新同一行
而不是新建，这样"已成功的目的地跳过不重发"和"原地编辑已发消息"才有地方落）。

只有本模块的 service.py 能 import 这里的东西。`account_id`/`intent_id`/
`discovery_run_id`/`connection_id` 全部跨 domain 引用，刻意不建外键，约定
见 `domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库
外键」一节；`report_id` 是本 domain 内部引用，仍然是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK
    intent_id: Mapped[int] = mapped_column(index=True)  # 同上（domain.intents）
    discovery_run_id: Mapped[int] = mapped_column(index=True)  # 同上（domain.intents）

    status: Mapped[str] = mapped_column(String(16))  # "success" | "failed"
    title_screen_status: Mapped[str] = mapped_column(String(16), default="not_started")
    analysis_status: Mapped[str] = mapped_column(String(16), default="not_started")

    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    title_kept_count: Mapped[int] = mapped_column(Integer, default=0)
    abstract_candidate_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)


class ReportItem(Base):
    __tablename__ = "report_items"
    __table_args__ = (
        UniqueConstraint("report_id", "source", "source_id", name="uq_report_item_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)

    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSON)

    title_screen_status: Mapped[str] = mapped_column(String(16), default="not_started")
    title_screen_reason: Mapped[str | None] = mapped_column(Text)
    title_relevance_score: Mapped[float | None] = mapped_column(Float)

    analysis_status: Mapped[str] = mapped_column(String(16), default="not_started")
    analysis_reason: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    novelty_score: Mapped[float | None] = mapped_column(Float)
    evidence_strength_score: Mapped[float | None] = mapped_column(Float)
    human_relevance_score: Mapped[float | None] = mapped_column(Float)
    recommend: Mapped[bool | None] = mapped_column(Boolean)
    priority: Mapped[str | None] = mapped_column(String(16))
    tags_json: Mapped[list | None] = mapped_column(JSON)
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("report_id", "connection_id", name="uq_delivery_report_connection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    connection_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.connections）

    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|success|failed
    message_ids_json: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)
