"""E4 Telegram 报告投递与目的地管理。"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from sqlalchemy.orm import Session

from adapters.delivery import telegram
from caps.httpfetch import default_resolve
from caps.probe import run_check
from caps.secrets import derive_key
from domain.connections import (
    ConnectionDTO,
    create_connection,
    get_connection,
    get_decrypted_secret,
    list_connections,
    record_check_result,
)
from domain.reports import (
    DeliveryDTO,
    get_delivery,
    get_or_create_delivery,
    get_report,
    list_report_items,
    update_delivery_status,
)
from infra.config import settings
from infra.errors import AppError, Conflict, PermissionDenied

KIND = "telegram_destination"
_CONNECTIONS_SECRET_PURPOSE = "connections"
_MAX_PART_LENGTH = 4096


class DeliveryError(AppError):
    status_code = 502
    code = "report_delivery_failed"


class RefreshNotPossible(Conflict):
    code = "delivery_refresh_not_possible"


@dataclass(frozen=True)
class DeliveryOutcome:
    destination_id: int
    delivery: DeliveryDTO
    status: str  # sent | skipped | failed


@dataclass(frozen=True)
class DeliveryBatchResult:
    outcomes: tuple[DeliveryOutcome, ...]


def _secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose=_CONNECTIONS_SECRET_PURPOSE)


def _require_destination(connection: ConnectionDTO) -> None:
    if connection.kind != KIND:
        raise ValueError(f"连接 #{connection.id} 不是 {KIND!r}")


def _require_report_owner(db: Session, *, account_id: int, report_id: int):
    report = get_report(db, report_id)
    if report.account_id != account_id:
        raise PermissionDenied("无权投递这份报告")
    if report.status != "success":
        raise DeliveryError("发现运行失败，不能投递报告")
    return report


def create_telegram_destination(
    db: Session, *, account_id: int, name: str, bot_token: str, chat_id: str, enabled: bool = True
) -> ConnectionDTO:
    if not bot_token.strip() or not chat_id.strip():
        raise ValueError("bot_token 和 chat_id 均不能为空")
    return create_connection(
        db,
        account_id=account_id,
        kind=KIND,
        name=name,
        config={"chat_id": chat_id},
        secret_plain=bot_token,
        secret_key=_secret_key(),
        enabled=enabled,
    )


def list_telegram_destinations(db: Session, *, account_id: int) -> list[ConnectionDTO]:
    return list_connections(db, account_id=account_id, kind=KIND)


def check_telegram_destination(
    db: Session, destination_id: int, *, transport=None, resolve=default_resolve
) -> ConnectionDTO:
    connection = get_connection(db, destination_id)
    _require_destination(connection)
    token = get_decrypted_secret(db, destination_id, secret_key=_secret_key())
    result = run_check(
        lambda config: telegram.check(config, transport=transport, resolve=resolve),
        {"bot_token": token, "chat_id": connection.config.get("chat_id")},
    )
    return record_check_result(db, destination_id, ok=result.ok, message=result.message)


def render_report_parts(db: Session, *, report_id: int) -> tuple[str, ...]:
    report = get_report(db, report_id)
    candidates = [item for item in list_report_items(db, report_id=report_id) if item.recommend]
    candidates.sort(
        key=lambda item: (item.relevance_score or 0, item.evidence_strength_score or 0), reverse=True
    )
    lines = [
        f"<b>研究报告 #{report.id}</b>",
        f"候选 {report.candidate_count} · 标题保留 {report.title_kept_count} · "
        f"摘要分析 {report.abstract_candidate_count}",
    ]
    for item in candidates[:3]:
        title = escape(item.title or "（无标题）")
        reason = escape(item.analysis_reason or "")
        scores = f"相关性：{item.relevance_score or 0:.2f}；证据：{item.evidence_strength_score or 0:.2f}"
        lines.extend([f"\n<b>{title}</b>", scores, reason])
    if not candidates:
        lines.append("\n本轮没有推荐条目。")
    parts: list[str] = []
    current = ""
    for line in lines:
        addition = f"{line}\n"
        if current and len(current) + len(addition) > _MAX_PART_LENGTH:
            parts.append(current.rstrip())
            current = ""
        current += addition
    if current:
        parts.append(current.rstrip())
    return tuple(parts)


def deliver_report(
    db: Session, *, account_id: int, report_id: int, transport=None, resolve=default_resolve
) -> DeliveryBatchResult:
    report = _require_report_owner(db, account_id=account_id, report_id=report_id)
    parts = render_report_parts(db, report_id=report.id)
    destinations = list_connections(db, account_id=account_id, kind=KIND, enabled_only=True)
    if not destinations:
        raise DeliveryError("没有启用的 Telegram 投递目的地")
    outcomes: list[DeliveryOutcome] = []
    for destination in destinations:
        delivery = get_or_create_delivery(db, report_id=report.id, connection_id=destination.id)
        if delivery.status == "success":
            outcomes.append(DeliveryOutcome(destination.id, delivery, "skipped"))
            continue
        token = get_decrypted_secret(db, destination.id, secret_key=_secret_key())
        try:
            message_ids = [
                telegram.send_message(
                    bot_token=token,
                    chat_id=destination.config["chat_id"],
                    text=part,
                    transport=transport,
                    resolve=resolve,
                )
                for part in parts
            ]
        except telegram.TelegramError as exc:
            delivery = update_delivery_status(db, delivery.id, status="failed", error=str(exc))
            outcomes.append(DeliveryOutcome(destination.id, delivery, "failed"))
        else:
            delivery = update_delivery_status(
                db, delivery.id, status="success", message_ids=message_ids, error=None
            )
            outcomes.append(DeliveryOutcome(destination.id, delivery, "sent"))
    if not any(outcome.status in {"sent", "skipped"} for outcome in outcomes):
        raise DeliveryError("所有 Telegram 目的地投递失败")
    return DeliveryBatchResult(tuple(outcomes))


def refresh_delivery(
    db: Session, *, account_id: int, delivery_id: int, transport=None, resolve=default_resolve
) -> DeliveryDTO:
    delivery = get_delivery(db, delivery_id)
    _require_report_owner(db, account_id=account_id, report_id=delivery.report_id)
    if delivery.status != "success" or not delivery.message_ids:
        raise RefreshNotPossible("该投递没有可原地编辑的成功消息")
    parts = render_report_parts(db, report_id=delivery.report_id)
    if len(parts) != len(delivery.message_ids):
        raise RefreshNotPossible("新旧消息分段数不同，请重新投递")
    destination = get_connection(db, delivery.connection_id)
    _require_destination(destination)
    token = get_decrypted_secret(db, destination.id, secret_key=_secret_key())
    try:
        for message_id, part in zip(delivery.message_ids, parts, strict=True):
            telegram.edit_message(
                bot_token=token,
                chat_id=destination.config["chat_id"],
                message_id=message_id,
                text=part,
                transport=transport,
                resolve=resolve,
            )
    except telegram.TelegramError as exc:
        return update_delivery_status(db, delivery.id, status="failed", error=str(exc))
    return update_delivery_status(db, delivery.id, status="success", error=None)
