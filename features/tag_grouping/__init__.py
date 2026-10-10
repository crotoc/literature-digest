"""features/tag_grouping。详见 contract.py。"""

from features.tag_grouping.contract import (
    JOB_KIND_MERGE,
    MergeResult,
    TagClusterProposal,
    TagGroupingError,
    merge_tags,
    propose_ai_duplicates,
    propose_ai_groups,
)

__all__ = [
    "JOB_KIND_MERGE",
    "MergeResult",
    "TagClusterProposal",
    "TagGroupingError",
    "merge_tags",
    "propose_ai_duplicates",
    "propose_ai_groups",
]
