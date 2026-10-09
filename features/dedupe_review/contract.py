"""features/dedupe_review 的对外表面。外部只许 import 这里的东西。"""

from features.dedupe_review.service import (
    JOB_KIND_DISMISS,
    JOB_KIND_ITEM,
    BatchOutcome,
    BatchResult,
    CandidatePairDTO,
    bulk_dismiss_candidates,
    list_pending_candidates,
    merge_works,
)

__all__ = [
    "JOB_KIND_DISMISS",
    "JOB_KIND_ITEM",
    "BatchOutcome",
    "BatchResult",
    "CandidatePairDTO",
    "bulk_dismiss_candidates",
    "list_pending_candidates",
    "merge_works",
]
