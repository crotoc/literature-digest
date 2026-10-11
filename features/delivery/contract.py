"""features/delivery 的对外表面。"""

from features.delivery.service import (
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

__all__ = [
    "DeliveryBatchResult",
    "DeliveryError",
    "DeliveryOutcome",
    "RefreshNotPossible",
    "check_telegram_destination",
    "create_telegram_destination",
    "deliver_report",
    "list_telegram_destinations",
    "refresh_delivery",
]
