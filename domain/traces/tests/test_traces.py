"""domain/traces 单测：轨迹创建、步骤记录、查询。"""

import pytest
from sqlalchemy.orm import Session

from domain.traces.contract import TraceNotFound
from domain.traces.service import add_step, create_trace, finalize_trace, get_trace, list_traces_for_work
from infra.db import new_memory_session


@pytest.fixture
def db_session():
    """内存 SQLite 测试数据库（自动创建所有表）。"""
    session = new_memory_session()
    yield session
    session.close()


def test_create_trace(db_session: Session):
    """新建轨迹记录。"""
    trace = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=1,
        job_id=1,
        source_url="https://example.com/paper.pdf",
    )
    assert trace.id
    assert trace.work_id == 1
    assert trace.source_url == "https://example.com/paper.pdf"


def test_add_step(db_session: Session):
    """记录步骤。"""
    trace = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=1,
        job_id=1,
        source_url="https://example.com/paper.pdf",
    )
    # add_step 返回 None，只是记录，之后查询验证
    add_step(
        db_session,
        trace_id=trace.id,
        step_type="observation",
        observation={"url": "https://example.com", "has_captcha": False},
    )
    # 查询验证步骤被记录
    retrieved = get_trace(db_session, trace.id)
    assert len(retrieved.steps) == 1
    assert retrieved.steps[0].step_type == "observation"


def test_finalize_and_get(db_session: Session):
    """完成轨迹并查询。"""
    trace = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=1,
        job_id=1,
        source_url="https://example.com/paper.pdf",
    )
    finalize_trace(db_session, trace.id, final_status="success")

    retrieved = get_trace(db_session, trace.id)
    assert retrieved.id == trace.id
    assert retrieved.final_status == "success"


def test_list_traces_for_work(db_session: Session):
    """列出某个文献的所有轨迹。"""
    trace1 = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=1,
        job_id=1,
        source_url="https://example.com/paper1.pdf",
    )
    trace2 = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=1,
        job_id=2,
        source_url="https://example.com/paper2.pdf",
    )

    traces = list_traces_for_work(db_session, work_id=1)
    assert len(traces) == 2


def test_trace_not_found(db_session: Session):
    """查询不存在的轨迹。"""
    with pytest.raises(TraceNotFound):
        get_trace(db_session, trace_id=9999)
