import pytest

from domain.reports import (
    DeliveryNotFound,
    ReportItemNotFound,
    ReportNotFound,
    create_report,
    create_report_item,
    get_delivery,
    get_or_create_delivery,
    get_report,
    get_report_item,
    list_deliveries,
    list_deliveries_for_report,
    list_report_items,
    list_reports,
    update_delivery_status,
    update_report,
    update_report_item_analysis,
    update_report_item_metadata,
    update_report_item_title_screen,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2


def _make_report(db, *, account_id=ACCOUNT, intent_id=10, discovery_run_id=20, **kwargs):
    return create_report(
        db,
        account_id=account_id,
        intent_id=intent_id,
        discovery_run_id=discovery_run_id,
        status="success",
        **kwargs,
    )


def test_create_report_and_update_its_funnel_fields(db):
    report = _make_report(db, candidate_count=7)
    assert report.title_screen_status == "not_started"
    updated = update_report(
        db,
        report.id,
        title_screen_status="partial",
        analysis_status="success",
        title_kept_count=4,
        abstract_candidate_count=3,
    )
    assert updated.title_screen_status == "partial"
    assert updated.analysis_status == "success"
    assert (updated.candidate_count, updated.title_kept_count, updated.abstract_candidate_count) == (7, 4, 3)


def test_report_rejects_unknown_statuses_and_missing_id(db):
    with pytest.raises(ValueError):
        create_report(db, account_id=ACCOUNT, intent_id=1, discovery_run_id=1, status="running")
    with pytest.raises(ReportNotFound):
        get_report(db, 999)
    with pytest.raises(ReportNotFound):
        update_report(db, 999, analysis_status="success")
    report = _make_report(db)
    with pytest.raises(ValueError):
        update_report(db, report.id, analysis_status="unknown")


def test_list_reports_is_account_scoped_and_optionally_filters_intent(db):
    mine = _make_report(db, intent_id=101)
    _make_report(db, intent_id=102)
    _make_report(db, account_id=OTHER_ACCOUNT, intent_id=101)
    assert [row.id for row in list_reports(db, account_id=ACCOUNT, intent_id=101)] == [mine.id]
    assert len(list_reports(db, account_id=ACCOUNT)) == 2


def test_report_item_is_idempotent_and_cannot_orphan(db):
    report = _make_report(db)
    first = create_report_item(db, report_id=report.id, source="pubmed", source_id="123", title="Initial")
    repeated = create_report_item(db, report_id=report.id, source="pubmed", source_id="123", title="Ignored")
    assert repeated.id == first.id
    assert get_report_item(db, first.id).title == "Initial"
    with pytest.raises(ReportNotFound):
        create_report_item(db, report_id=999, source="pubmed", source_id="missing")


def test_report_item_metadata_title_screen_and_analysis_lifecycle(db):
    report = _make_report(db)
    item = create_report_item(db, report_id=report.id, source="crossref", source_id="doi:1")
    metadata = update_report_item_metadata(
        db, item.id, title="A title", url="https://example.test", payload={"abstract": "x"}
    )
    assert metadata.payload == {"abstract": "x"}
    screened = update_report_item_title_screen(
        db, item.id, status="keep", relevance_score=0.8, reason="on topic"
    )
    assert screened.title_screen_status == "keep"
    analyzed = update_report_item_analysis(
        db,
        item.id,
        status="success",
        relevance_score=0.9,
        novelty_score=0.4,
        evidence_strength_score=0.7,
        human_relevance_score=0.6,
        recommend=True,
        priority="high",
        tags=["AD", "method"],
        raw={"why": "useful"},
        model="test-model",
        prompt_id="report-v1",
    )
    assert analyzed.recommend is True
    assert analyzed.tags == ["AD", "method"]
    kept = list_report_items(db, report_id=report.id, title_screen_status="keep")
    assert [row.id for row in kept] == [item.id]


def test_report_item_rejects_unknown_statuses_and_missing_id(db):
    report = _make_report(db)
    item = create_report_item(db, report_id=report.id, source="pubmed", source_id="123")
    with pytest.raises(ValueError):
        update_report_item_title_screen(db, item.id, status="maybe")
    with pytest.raises(ValueError):
        update_report_item_analysis(db, item.id, status="queued")
    with pytest.raises(ReportItemNotFound):
        get_report_item(db, 999)


def test_delivery_is_idempotent_scoped_and_updates_message_ids(db):
    report = _make_report(db, intent_id=9)
    other_report = _make_report(db, account_id=OTHER_ACCOUNT, intent_id=9)
    first = get_or_create_delivery(db, report_id=report.id, connection_id=44)
    repeated = get_or_create_delivery(db, report_id=report.id, connection_id=44)
    get_or_create_delivery(db, report_id=other_report.id, connection_id=44)
    assert repeated.id == first.id
    updated = update_delivery_status(db, first.id, status="success", message_ids=["100", "101"])
    assert updated.status == "success"
    assert updated.message_ids == ["100", "101"]
    assert [row.id for row in list_deliveries_for_report(db, report.id)] == [first.id]
    assert [row.id for row in list_deliveries(db, account_id=ACCOUNT, intent_id=9)] == [first.id]


def test_delivery_rejects_orphan_unknown_status_and_missing_id(db):
    with pytest.raises(ReportNotFound):
        get_or_create_delivery(db, report_id=999, connection_id=1)
    with pytest.raises(DeliveryNotFound):
        get_delivery(db, 999)
    report = _make_report(db)
    delivery = get_or_create_delivery(db, report_id=report.id, connection_id=2)
    with pytest.raises(ValueError):
        update_delivery_status(db, delivery.id, status="sending")
