"""features/dedupe_review。详见 contract.py。"""

from features.dedupe_review.contract import (
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
