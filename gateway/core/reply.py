from dataclasses import dataclass
from enum import StrEnum

REFUSAL_TEXT = "I can't help with that request. It was blocked by the organization's AI usage policy."
FAILED_CLOSED_TEXT = "The assistant is temporarily unavailable, so your request was not processed. Please try again later."


class ReplyOutcome(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"
    FAILED_CLOSED = "failed_closed"


class DeniedAt(StrEnum):
    CHECKPOINT_1 = "checkpoint_1"
    CHECKPOINT_2 = "checkpoint_2"


@dataclass(frozen=True)
class GatewayReply:
    request_id: str
    outcome: ReplyOutcome
    text: str
