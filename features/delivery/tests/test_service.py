import httpx
import pytest

from domain.reports import (
    create_report,
    create_report_item,
    get_or_create_delivery,
    update_report_item_analysis,
)
from features.delivery import DeliveryError, create_telegram_destination, deliver_report

ACCOUNT = 1
_FAKE_RESOLVE = lambda host: ["149.154.167.220"]  # noqa: E731


def _report(db):
    report = create_report(
        db, account_id=ACCOUNT, intent_id=1, discovery_run_id=1, status="success", candidate_count=1
    )
    item = create_report_item(db, report_id=report.id, source="pubmed", source_id="1", title="A <paper>")
    update_report_item_analysis(
        db,
        item.id,
        status="success",
        relevance_score=0.9,
        evidence_strength_score=0.8,
        recommend=True,
        reason="useful",
    )
    return report


def _transport(status=200, payload=None):
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload or {"ok": True, "result": {"message_id": 42}})
    )


def test_deliver_sends_once_then_skips_existing_success(db):
    report = _report(db)
    destination = create_telegram_destination(
        db, account_id=ACCOUNT, name="lab", bot_token="123:abc", chat_id="-1001"
    )
    first = deliver_report(
        db, account_id=ACCOUNT, report_id=report.id, transport=_transport(), resolve=_FAKE_RESOLVE
    )
    second = deliver_report(
        db, account_id=ACCOUNT, report_id=report.id, transport=_transport(), resolve=_FAKE_RESOLVE
    )
    assert first.outcomes[0].status == "sent"
    assert second.outcomes[0].status == "skipped"
    assert first.outcomes[0].delivery.connection_id == destination.id


def test_all_failed_destinations_raise_and_record_failure(db):
    report = _report(db)
    destination = create_telegram_destination(
        db, account_id=ACCOUNT, name="lab", bot_token="123:abc", chat_id="-1001"
    )
    with pytest.raises(DeliveryError):
        deliver_report(
            db,
            account_id=ACCOUNT,
            report_id=report.id,
            transport=_transport(payload={"ok": False, "description": "blocked"}),
            resolve=_FAKE_RESOLVE,
        )
    delivery = get_or_create_delivery(db, report_id=report.id, connection_id=destination.id)
    assert delivery.status == "failed"
    assert "blocked" in delivery.error
