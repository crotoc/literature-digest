"""E4 报告投递；外部只从 contract 导入。"""

from features.delivery.contract import (  # noqa: F401
    DeliveryBatchResult,
    DeliveryError,
    DeliveryOutcome,
    RefreshNotPossible,
    check_telegram_destination,
    create_telegram_destination,
    deliver_report,
    list_telegram_destinations,
    refresh_delivery,
)
