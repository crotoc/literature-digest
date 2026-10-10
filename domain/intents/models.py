"""domain/intents 的 ORM 模型：研究意图 + 它的版本化查询编译结果 +
发现运行记录 + 候选文献 + 去重"已见过"标记。

五张表的关系：一个 `Intent` 有多个 `IntentVersion`（每次重新编译查询产生
一个新版本，不覆盖旧版本——`features/discovery` 只用最新那个 `status=
"ready"` 的版本）；每次 `features/discovery` 跑一轮发现产生一个
`DiscoveryRun`（绑定当时用的那个版本 + 一个 `domain/jobs` 的 job_id）；
这一轮新发现的（排除已经见过的）文献落成若干 `Candidate` 行；
`IntentSeen` 只记"这个 intent 见过这条来源记录了"，不存文献本身的数据，
是跨多轮发现去重用的轻量标记表。

只有本模块的 service.py 能 import 这里的东西。`account_id` 跨 domain
引用，刻意不建外键，约定见 `domain/libraries/README.md`「`LibraryMember.
account_id` 刻意不是数据库外键」一节；`job_id` 同理（引用 `domain.jobs`）。
本 domain 内部的引用（`intent_id`/`intent_version_id`/`discovery_run_id`）
仍然是真 FK。
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infra.db import Base


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间——原因见 domain/accounts/models.py 同名函数的
    docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Intent(Base):
    __tablename__ = "intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用，刻意不建 FK

    name: Mapped[str] = mapped_column(String(200))
    question_text: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    candidate_limit: Mapped[int] = mapped_column(Integer, default=40)
    lookback_days: Mapped[int] = mapped_column(Integer, default=7)
    query_override: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(insert_default=_utcnow, onupdate=_utcnow)


class IntentVersion(Base):
    __tablename__ = "intent_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("intents.id"), index=True)

    query_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(16))  # "ai" | "override"
    model: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16))  # "ready" | "failed"
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("intents.id"), index=True)
    intent_version_id: Mapped[int] = mapped_column(ForeignKey("intent_versions.id"))
    job_id: Mapped[int] = mapped_column(index=True)  # 跨 domain 引用（domain.jobs），刻意不建 FK

    status: Mapped[str] = mapped_column(String(16), default="running")  # running|succeeded|failed
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    new_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column()


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("intent_id", "source", "source_id", name="uq_candidate_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discovery_run_id: Mapped[int] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("intents.id"), index=True)

    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)


class IntentSeen(Base):
    __tablename__ = "intent_seen"
    __table_args__ = (UniqueConstraint("intent_id", "source", "source_id", name="uq_intent_seen_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("intents.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(128))

    first_seen_at: Mapped[datetime] = mapped_column(insert_default=_utcnow)
