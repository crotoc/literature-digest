"""domain/jobs 的对外表面。外部只许 import 这里的东西，不碰 models.py。"""

from domain.jobs.service import (
    DEFAULT_STATUS,
    STATUSES,
    TERMINAL_STATUSES,
    InvalidTransition,
    JobDTO,
    JobNotFound,
    create_job,
    get_job,
    list_child_jobs,
    list_jobs,
    retry_job,
    transition,
    update_counts,
    update_cursor,
)

__all__ = [
    "DEFAULT_STATUS",
    "STATUSES",
    "TERMINAL_STATUSES",
    "InvalidTransition",
    "JobDTO",
    "JobNotFound",
    "create_job",
    "get_job",
    "list_child_jobs",
    "list_jobs",
    "retry_job",
    "transition",
    "update_counts",
    "update_cursor",
]
