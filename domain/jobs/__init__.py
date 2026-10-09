"""domain/jobs。详见 contract.py。"""

from domain.jobs.contract import (
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
