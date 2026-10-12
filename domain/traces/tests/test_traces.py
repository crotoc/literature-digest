"""domain/traces 单测：轨迹创建、步骤记录、查询。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from domain.traces.contract import TraceNotFound
from domain.traces.service import add_step, create_trace, finalize_trace, get_trace, list_traces_for_work
from infra.db import Base


@pytest.fixture
def db_session():
    """内存 SQLite 测试数据库，仅建 traces 表。"""
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    # 创建模型时会因为 FK 约束而失败，所以明确只建 traces 和 trace_steps 表
    for table in Base.metadata.tables.values():
        if table.name in ("traces", "trace_steps"):
            table.create(engine)
    from sqlalchemy.orm import sessionmaker
    SessionMaker = sessionmaker(engine)
    session = SessionMaker()
    yield session
    session.close()


def test_create_trace(db_session: Session):
    """新建轨迹记录。"""
    trace = create_trace(
        db_session,
        account_id=1,
        library_id=1,
        work_id=100,
        job_id=1,
        source_url="https://example.com/paper.html",
    )
    assert trace.work_id == 100
    assert trace.source_url == "https://example.com/paper.html"
    assert trace.final_status == "pending"
    assert len(trace.steps) == 0
    db_session.commit()


def test_add_step(db_session: Session):
    """向轨迹添加步骤。"""
    trace = create_trace(db_session, account_id=1, library_id=1, work_id=100, job_id=1, source_url="http://x.com")
    
    add_step(
        db_session,
        trace_id=trace.id,
        step_type="resolver",
        action="generic_html",
        observation={"url": "http://x.com", "found_pdf_link": True},
    )
    
    db_session.commit()
    
    trace_reloaded = get_trace(db_session, trace.id)
    assert len(trace_reloaded.steps) == 1
    assert trace_reloaded.steps[0].step_type == "resolver"


def test_finalize_trace(db_session: Session):
    """设置轨迹最终状态。"""
    trace = create_trace(db_session, account_id=1, library_id=1, work_id=100, job_id=1, source_url="http://x.com")
    
    finalize_trace(db_session, trace.id, final_status="success")
    db_session.commit()
    
    trace_final = get_trace(db_session, trace.id)
    assert trace_final.final_status == "success"
    assert trace_final.completed_at is not None


def test_list_traces_for_work(db_session: Session):
    """查询某篇文献的历史轨迹。"""
    t1 = create_trace(db_session, account_id=1, library_id=1, work_id=100, job_id=1, source_url="http://x1.com")
    t2 = create_trace(db_session, account_id=1, library_id=1, work_id=100, job_id=2, source_url="http://x2.com")
    t3 = create_trace(db_session, account_id=1, library_id=1, work_id=200, job_id=3, source_url="http://x3.com")
    
    finalize_trace(db_session, t1.id, "success")
    finalize_trace(db_session, t2.id, "failed", "no_candidate")
    
    db_session.commit()
    
    traces = list_traces_for_work(db_session, work_id=100)
    assert len(traces) == 2
    assert all(t.work_id == 100 for t in traces)


def test_trace_not_found(db_session: Session):
    """查询不存在的轨迹。"""
    with pytest.raises(TraceNotFound):
        get_trace(db_session, 999)
