"""One Lark turn keeps progress and the final answer in the same message.

A later progress edit must not replace an answer that already went out.
Cancel and failure replace an unfinished progress message instead of leaving
the last tool line as if it were the result.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import asyncio


CANCELLED_TEXT = "这次已经停了。要继续，请再发一次。"
FAILURE_TEXT = "这次没做完。请再试一次。"
UNREADABLE_ATTACHMENT = "有附件没能读出来。请重新发一次，或改用文字说明。"
# Lark allows 20 edits per text/post message; reserve two for delivery and repair.
MAX_PROGRESS_EDITS = 18
TRANSPORT_TIMEOUT = 30

_turn: ContextVar["TurnReply | None"] = ContextVar("lark_topic_reply", default=None)


@dataclass
class TurnReply:
    event_id: str
    message_id: str | None = None
    delivered: bool = False
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    progress_edits: int = 0
    visible_text: str | None = None
    final_preview: str | None = None
    final_chunks: list[str] = field(default_factory=list)
    next_final_chunk: int = 0


async def settle_on_cancel(operation):
    """Keep the transport receipt before propagating cancellation to its caller."""
    task = asyncio.create_task(asyncio.wait_for(operation, timeout=TRANSPORT_TIMEOUT))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except BaseException:
            # A receipt is uncertain; never open another reply for this turn.
            current().closed = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            raise
    except asyncio.TimeoutError:
        current().closed = True
        raise


def begin(event_id: str) -> None:
    _turn.set(TurnReply(event_id))


def current() -> TurnReply | None:
    return _turn.get()


def decide_send(metadata: dict | None) -> str:
    """How this send should treat the turn's single message.

    ``create`` is the first progress bubble. ``edit`` updates that bubble.
    ``ignore`` drops a late progress send. ``pass`` leaves the official send alone.
    """
    turn = current()
    if turn is None:
        return "pass"
    final = bool((metadata or {}).get("notify"))
    if turn.closed or turn.delivered:
        return "ignore"
    if turn.final_chunks and not final:
        return "ignore"
    if final or turn.message_id:
        return "edit" if turn.message_id else "pass"
    return "create"


def note_created(message_id: str) -> None:
    turn = current()
    if turn is not None and message_id and not turn.message_id:
        turn.message_id = message_id


def note_delivered() -> None:
    turn = current()
    if turn is not None:
        turn.delivered = True


def decide_edit(message_id: str) -> str:
    """Late edits of the answer message are ignored. Other messages pass through."""
    turn = current()
    if turn is None or not turn.message_id or message_id != turn.message_id:
        return "pass"
    if turn.closed or turn.delivered:
        return "ignore"
    return "pass"


def closing_text(event_id: str, outcome: str) -> str | None:
    turn = current()
    if turn is None or turn.event_id != event_id:
        return None
    turn.closed = True
    if not turn.message_id or turn.delivered:
        return None
    if outcome == "cancelled":
        return CANCELLED_TEXT
    if outcome == "failure":
        return FAILURE_TEXT
    return None


def clip_progress(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]


def split_final(text: str, limit: int) -> list[str]:
    if not text:
        return []
    if limit <= 0 or len(text) <= limit:
        return [text]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def attachment_note(expected: int, saved: int) -> str | None:
    if expected > saved:
        return UNREADABLE_ATTACHMENT
    return None
