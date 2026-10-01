"""Feishu adapter seam: key the discussion before batching, and send into a real topic.

The official class stays responsible for connection, auth, and transport. If the
methods this file overrides disappear, registration must fail instead of silently
keeping the stock topic behavior.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from hermes_constants import get_hermes_home
from plugins.platforms.feishu.adapter import FeishuAdapter

from .routing import InboundDecision, TopicStore, is_server_thread_id, thread_fields_from_response

logger = logging.getLogger(__name__)

REQUIRED_SEAMS = ("_dispatch_inbound_event", "_admit", "_send_raw_message")


class TopicSendRefusal:
    """A failed send result that never falls through to a top-level create."""

    def __init__(self, message: str):
        self.code = "lark_topic_send_refused"
        self.msg = message
        self.data = None

    def success(self) -> bool:
        return False


def missing_seams(adapter_cls: type) -> list[str]:
    return [name for name in REQUIRED_SEAMS if not callable(getattr(adapter_cls, name, None))]


class LarkTopicAdapter(FeishuAdapter):
    def __init__(self, config: Any, *, store: Optional[TopicStore] = None):
        super().__init__(config)
        self._topics = store or TopicStore(get_hermes_home() / "lark-topics" / "topics.json")
        logger.info("[LarkTopics] topic adapter ready")
        self._inbound_routes: dict[str, InboundDecision] = {}
        self._context_prefixes: dict[str, str] = {}
        self._speaker_claims: dict[str, str] = {}

    def _admit(self, sender: Any, message: Any):
        reason = super()._admit(sender, message)
        if reason != "group_policy_rejected":
            return reason
        chat_id = getattr(message, "chat_id", "") or ""
        sender_id = getattr(sender, "sender_id", None)
        if not self._allow_group_message(sender_id, chat_id, is_bot=_sender_is_bot(sender)):
            return reason
        if not self._require_mention_for(chat_id) or self._mentions_self(message):
            return reason
        if self._topics.allows_unmentioned(
            app_id=self._app_id or "",
            chat_id=chat_id,
            thread_id=getattr(message, "thread_id", None),
            parent_id=getattr(message, "parent_id", None),
            root_id=getattr(message, "root_id", None),
            sender_id=_sender_open_id(sender_id),
        ):
            return None
        return reason

    async def _process_inbound_message(
        self, *, data: Any, message: Any, sender_id: Any, chat_type: str, message_id: str, is_bot: bool = False,
    ) -> None:
        sender = _sender_open_id(sender_id)
        mentioned = self._mentions_self(message)
        preview = _message_preview(message)
        decision = self._topics.route_inbound(
            app_id=self._app_id or "",
            chat_id=getattr(message, "chat_id", "") or "",
            chat_type=chat_type,
            message_id=message_id,
            thread_id=getattr(message, "thread_id", None),
            parent_id=getattr(message, "parent_id", None) or getattr(message, "upper_message_id", None),
            root_id=getattr(message, "root_id", None),
            sender_id=sender,
            mentioned=mentioned,
            is_bot=is_bot,
            mentions_other=self._mentions_other(message),
            text=preview,
        )
        self._inbound_routes[message_id] = decision
        if decision.discussion_key and not decision.deliver:
            self._inbound_routes.pop(message_id, None)
            logger.info("[LarkTopics] kept a topic message without replying")
            return
        if (
            decision.discussion_key
            and _is_foreign_control(preview)
            and not self._topics.sender_is_active(self._app_id or "", getattr(message, "chat_id", "") or "", decision.discussion_key, sender)
        ):
            self._inbound_routes.pop(message_id, None)
            logger.info("[LarkTopics] ignored a control request from someone other than the current speaker")
            return
        if decision.context:
            self._context_prefixes[message_id] = decision.context
        if mentioned and sender and decision.discussion_key:
            self._speaker_claims[message_id] = sender
        try:
            await super()._process_inbound_message(
                data=data, message=message, sender_id=sender_id, chat_type=chat_type,
                message_id=message_id, is_bot=is_bot,
            )
        finally:
            self._inbound_routes.pop(message_id, None)

    async def _dispatch_inbound_event(self, event: Any) -> None:
        message_id = getattr(event, "message_id", "") or ""
        prefix = self._context_prefixes.pop(message_id, None)
        if prefix:
            current_text = getattr(event, "text", "") or ""
            event.text = f"{prefix}\n\n{current_text}" if current_text else prefix
        decision = self._inbound_routes.get(message_id)
        session_thread = decision.session_thread_id if decision is not None else None
        current = getattr(getattr(event, "source", None), "thread_id", None)
        if session_thread and session_thread != current:
            from gateway.session_identity import replace_source
            event.source = replace_source(event.source, thread_id=session_thread)
        await super()._dispatch_inbound_event(event)

    def _start_session_processing(self, event: Any, session_key: str, **kwargs: Any) -> bool:
        message_id = getattr(event, "message_id", "") or ""
        sender = self._speaker_claims.pop(message_id, None)
        source = getattr(event, "source", None)
        thread_id = getattr(source, "thread_id", None)
        chat_id = getattr(source, "chat_id", None)
        if sender and thread_id and chat_id:
            self._topics.claim_speaker(self._app_id or "", chat_id, thread_id, sender)
        return super()._start_session_processing(event, session_key, **kwargs)

    def _mentions_other(self, message: Any) -> bool:
        """True when the message @s someone besides the bot. @ the bot as well still counts as calling the bot."""
        mentions = getattr(message, "mentions", None) or []
        if not mentions:
            return False
        try:
            bot = self._bot_identity()
        except Exception:
            bot = None
        for mention in mentions:
            mention_id = getattr(mention, "id", None)
            open_id = (getattr(mention_id, "open_id", None) or "").strip()
            user_id = (getattr(mention_id, "user_id", None) or "").strip()
            name = (getattr(mention, "name", None) or "").strip()
            if bot is not None and bot.matches(open_id=open_id, user_id=user_id, name=name):
                continue
            if open_id or user_id or name:
                return True
        return False

    async def _send_raw_message(
        self, *, chat_id: str, msg_type: str, payload: str, reply_to: Optional[str],
        metadata: Optional[dict[str, Any]],
    ) -> Any:
        plan = self._topics.plan_outbound(
            app_id=self._app_id or "",
            chat_id=chat_id,
            metadata_thread_id=(metadata or {}).get("thread_id"),
            reply_to=reply_to,
        )
        if plan.mode == "official":
            return await super()._send_raw_message(
                chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=reply_to, metadata=metadata,
            )
        if plan.mode == "fail" or (plan.mode == "reply_in_thread" and not plan.reply_to):
            logger.warning("[LarkTopics] refusing send without a topic anchor in chat %s", chat_id)
            return TopicSendRefusal(plan.error or "refusing top-level topic send")

        translated = dict(metadata or {})
        translated["thread_id"] = plan.thread_id
        # Both topic modes carry their own reply target. The caller's reply_to
        # may be a main-chat quote; using it would open a second topic.
        if plan.mode in ("server_thread", "reply_in_thread"):
            target_reply = plan.reply_to
        else:
            target_reply = reply_to
        response = await super()._send_raw_message(
            chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=target_reply, metadata=translated,
        )
        if plan.discussion_key and self._response_succeeded(response):
            await self._bind_send_result(chat_id, plan.discussion_key, response)
        return response

    async def _bind_send_result(self, chat_id: str, discussion_key: str, response: Any) -> None:
        message_id, thread_id, root_id = thread_fields_from_response(response)
        if not is_server_thread_id(thread_id) and message_id:
            looked_up_thread, looked_up_root = await self._lookup_server_topic(message_id)
            thread_id = thread_id or looked_up_thread
            root_id = root_id or looked_up_root
        if not is_server_thread_id(thread_id):
            logger.warning("[LarkTopics] send succeeded but no server topic id was returned")
            return
        bound = self._topics.bind_server_topic(
            app_id=self._app_id or "", chat_id=chat_id, discussion_key=discussion_key,
            message_id=message_id, thread_id=thread_id, root_id=root_id,
        )
        if not bound:
            logger.warning("[LarkTopics] refused to rebind discussion %s to a different topic", discussion_key)

    async def _lookup_server_topic(self, message_id: str) -> tuple[Optional[str], Optional[str]]:
        if not self._client or not message_id:
            return None, None
        try:
            request = self._build_get_message_request(message_id)
            response = await self._run_blocking(self._client.im.v1.message.get, request)
        except Exception:
            logger.warning("[LarkTopics] failed to read back topic ids for %s", message_id, exc_info=True)
            return None, None
        if not self._response_succeeded(response):
            return None, None
        _message_id, thread_id, root_id = thread_fields_from_response(response)
        return thread_id, root_id


def _sender_open_id(sender_id: Any) -> Optional[str]:
    if sender_id is None:
        return None
    if isinstance(sender_id, str):
        return sender_id.strip() or None
    for name in ("open_id", "user_id", "union_id"):
        value = getattr(sender_id, name, None)
        if value:
            return str(value).strip() or None
    return None


def _message_preview(message: Any) -> str:
    raw = getattr(message, "content", "") or ""
    if not isinstance(raw, str):
        return ""
    try:
        import json
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return raw[:200]
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"][:200]
    return ""


_CONTROL_COMMANDS = ("/stop", "/approve", "/deny", "/new", "/reset")


def _is_foreign_control(text: str) -> bool:
    parts = (text or "").strip().split()
    while parts and parts[0].startswith("@"):
        parts.pop(0)
    if not parts:
        return False
    command = parts[0].split("@", 1)[0].lower()
    return any(command == name or command.startswith(name) for name in _CONTROL_COMMANDS)


def _sender_is_bot(sender: Any) -> bool:
    sender_type = str(getattr(sender, "sender_type", "") or "").lower()
    return sender_type == "app" or bool(getattr(sender, "is_bot", False))
