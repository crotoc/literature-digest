"""features/importing。详见 contract.py。"""

from features.importing.contract import (
    IDENTIFIER_SCHEMES_FOR_MATCHING,
    JOB_KIND,
    JOB_KIND_ITEM,
    OUTCOME_STATUSES,
    ImportItemOutcome,
    ImportParseFailed,
    ImportResult,
    import_records,
)

__all__ = [
    "IDENTIFIER_SCHEMES_FOR_MATCHING",
    "JOB_KIND",
    "JOB_KIND_ITEM",
    "OUTCOME_STATUSES",
    "ImportItemOutcome",
    "ImportParseFailed",
    "ImportResult",
    "import_records",
]
