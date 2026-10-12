"""features/fulltext 服务层：编排 resolvers、fetchers、fileprobe、storage。"""

from sqlalchemy.orm import Session

from domain.jobs.service import create_job, transition
from domain.jobs.contract import JobDTO
from domain.traces.service import create_trace, add_step, finalize_trace
from domain.attachments.service import create_attachment


def initiate_fulltext_download(
    db: Session,
    account_id: int,
    library_id: int,
    work_id: int,
    source_url: str,
) -> JobDTO:
    """
    启动一次全文下载任务。

    流程：
    1. 创建父 job（fulltext kind）
    2. 创建 trace 记录
    3. 下发初始 resolving 状态
    
    返回 job DTO，调用方可用 job_id 查询进度、记录观察等。
    """
    # 1. 新建 job
    job = create_job(
        db,
        account_id=account_id,
        library_id=library_id,
        kind="fulltext",
        source_url=source_url,
    )

    # 2. 新建 trace
    trace = create_trace(
        db,
        account_id=account_id,
        library_id=library_id,
        work_id=work_id,
        job_id=job.id,
        source_url=source_url,
    )

    # 3. 转移到 resolving
    transition(db, job.id, "resolving", reason="started", allowed=["queued"])
    db.commit()

    return job


def record_observation(db: Session, job_id: int, observation: dict) -> None:
    """记录扩展上报的页面观察到 trace。"""
    # 查找对应 trace（同 job_id）
    from domain.traces.models import Trace
    trace = db.query(Trace).filter_by(job_id=job_id).order_by(Trace.created_at.desc()).first()
    if not trace:
        raise ValueError(f"No trace for job {job_id}")

    add_step(
        db,
        trace_id=trace.id,
        step_type="observation",
        observation=observation,
    )
    db.commit()


def record_upload(db: Session, job_id: int, file_bytes: bytes, filename: str) -> None:
    """
    记录并存储上传的文件。

    流程：
    1. fileprobe 校验
    2. blobstore 存储
    3. attachments 创建
    4. job 转为 success
    """
    from caps.fileprobe import check_upload
    from caps.blobstore import put as blobstore_put
    import hashlib

    # 1. 校验
    try:
        check_upload(file_bytes, reject_damaged=True)
    except Exception as e:
        transition(db, job_id, "failed", reason=f"validation_failed: {str(e)}")
        db.commit()
        return

    # 2. 存 blobstore（内容寻址）
    digest = hashlib.sha256(file_bytes).hexdigest()
    blobstore_put(digest, file_bytes)

    # 3. 创建 attachment（需要 work_id，从 job context 读）
    # 先从 trace 反推 work_id
    from domain.traces.models import Trace
    trace = db.query(Trace).filter_by(job_id=job_id).first()
    if not trace:
        transition(db, job_id, "failed", reason="trace_not_found")
        db.commit()
        return

    attachment = create_attachment(
        db,
        library_id=trace.library_id,
        work_id=trace.work_id,
        filename=filename,
        digest=digest,
        size=len(file_bytes),
        content_type="application/pdf",
        role="main",
    )

    # 4. 记录成功
    from domain.traces.service import finalize_trace as trace_finalize
    trace_finalize(db, trace.id, final_status="success")
    transition(db, job_id, "success", reason="file_stored")
    db.commit()
