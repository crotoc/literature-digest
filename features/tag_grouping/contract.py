"""features/tag_grouping 的对外表面。外部只许 import 这里的东西。"""

from features.tag_grouping.service import (
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
