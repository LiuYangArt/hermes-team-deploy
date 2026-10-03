"""Feishu adapter seam: key the discussion before batching, and send into a real topic.

The official class stays responsible for connection, auth, and transport. If the
methods this file overrides disappear, registration must fail instead of silently
keeping the stock topic behavior.
"""

from __future__ import annotations

import logging
import json
import threading
from typing import Any, Optional

from gateway.platforms.base import SendResult
from hermes_constants import get_hermes_home
from plugins.platforms.feishu.adapter import FeishuAdapter

from . import replies
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
    def _is_interactive_operator_authorized(self, open_id: str) -> bool:
        """Shared-machine execution and software updates require a team admin."""
        if not open_id:
            return False
        root = get_hermes_home() / "lark-access"
        try:
            config = json.loads((root / "config.json").read_text())
            return open_id in config.get("admins", [])
        except (OSError, ValueError, AttributeError):
            return False

    def __init__(self, config: Any, *, store: Optional[TopicStore] = None):
        super().__init__(config)
        self._topics = store or TopicStore(get_hermes_home() / "lark-topics" / "topics.json")
        logger.info("[LarkTopics] topic adapter ready")
        self._inbound_routes: dict[str, InboundDecision] = {}
        self._context_prefixes: dict[str, str] = {}
        self._speaker_claims: dict[str, str] = {}
        self._approval_action_lock = threading.Lock()
        self._settled_approval_cards: dict[tuple[Any, str], dict[str, Any]] = {}

    async def _send_interactive_card(
        self, chat_id: str, card: dict[str, Any], metadata: Optional[dict[str, Any]], failure_message: str, *,
        state_map: dict[int, dict[str, str]], state_id: int, session_key: str,
    ) -> SendResult:
        is_approval = state_map is self._approval_state
        request_id = str((metadata or {}).get("exec_approval_request_id", "") or "")
        if is_approval and not request_id:
            logger.warning("[LarkTopics] refusing an approval card without one pending request id")
            return SendResult(success=False, error="approval request id is missing")
        result = await super()._send_interactive_card(
            chat_id, card, metadata, failure_message,
            state_map=state_map, state_id=state_id, session_key=session_key,
        )
        if is_approval and result.success:
            thread_id = str((metadata or {}).get("thread_id", "") or "")
            state = state_map.get(state_id)
            if state is not None:
                state.update({
                    "request_id": request_id,
                    "thread_id": thread_id,
                })
        return result

    @staticmethod
    def _expired_approval_card() -> dict[str, Any]:
        message = "这个批准请求已过期或已被处理。本次点击没有批准或执行任何命令，请查看本话题中的任务结果。"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"content": "批准已失效", "tag": "plain_text"},
                "template": "grey",
            },
            "elements": [{"tag": "markdown", "content": message}],
        }

    def _handle_approval_card_action(self, *, event: Any, action_value: dict[str, Any], loop: Any) -> Any:
        approval_id = action_value.get("approval_id")
        choices = {
            "approve_once": "once", "approve_session": "session",
            "approve_always": "always", "deny": "deny",
        }
        choice = choices.get(action_value.get("hermes_action"))
        if approval_id is None or choice is None:
            return self._card_response(self._expired_approval_card())
        lock = getattr(self, "_approval_action_lock", None)
        if lock is None:
            lock = self._approval_action_lock = threading.Lock()
        with lock:
            callback_message_id = str(getattr(getattr(event, "context", None), "open_message_id", "") or "")
            settled = getattr(self, "_settled_approval_cards", {})
            if (approval_id, callback_message_id) in settled:
                return self._card_response(settled[(approval_id, callback_message_id)])
            state = self._approval_state.get(approval_id)
            if not state:
                return self._card_response(self._expired_approval_card())
            expected_message_id = str(state.get("message_id", "") or "")
            if not expected_message_id or callback_message_id != expected_message_id:
                return self._card_response(self._expired_approval_card())
            checked = self._validate_card_action(
                event=event, state=state, label="approval", ident=approval_id,
            )
            if checked is None:
                return self._card_response()
            open_id, _chat_id, user_name = checked
            request_id = state.get("request_id", "")
            if not request_id:
                self._approval_state.pop(approval_id, None)
                return self._card_response(self._expired_approval_card())
            from tools.approval import resolve_gateway_approval
            count = resolve_gateway_approval(
                state.get("session_key", ""), choice, request_id=request_id,
            )
            self._approval_state.pop(approval_id, None)
            if count != 1:
                return self._card_response(self._expired_approval_card())
            resolved_card = self._build_resolved_approval_card(choice=choice, user_name=user_name)
            if len(settled) >= 256:
                settled.pop(next(iter(settled)))
            settled[(approval_id, callback_message_id)] = resolved_card
            self._settled_approval_cards = settled
            logger.info(
                "Lark approval resolved request %s (choice=%s, user=%s)",
                request_id, choice, open_id,
            )
            return self._card_response(resolved_card)

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

    async def on_processing_start(self, event: Any) -> None:
        message_id = getattr(event, "message_id", "") or ""
        if message_id:
            replies.begin(message_id)
        await super().on_processing_start(event)

    async def on_processing_complete(self, event: Any, outcome: Any) -> None:
        message_id = getattr(event, "message_id", "") or ""
        outcome_name = str(getattr(outcome, "value", outcome) or "")
        turn = replies.current()
        if turn is not None:
            async with turn.lock:
                if outcome_name == "success" and not turn.delivered and turn.final_preview and turn.message_id:
                    chat_id = getattr(getattr(event, "source", None), "chat_id", "") or ""
                    result = await self._reuse_reply(
                        chat_id, turn.message_id, turn.final_preview, message_id,
                        {"thread_id": getattr(event.source, "thread_id", None), "notify": True}, final=True,
                    )
                    if not result.success:
                        logger.warning("[LarkTopics] final preview delivery failed: %s", result.error)
                text = replies.closing_text(message_id, outcome_name)
                if text and turn.message_id:
                    chat_id = getattr(getattr(event, "source", None), "chat_id", "") or ""
                    result = await super().edit_message(chat_id, turn.message_id, text, finalize=True)
                    if not result.success:
                        logger.warning("[LarkTopics] reply cleanup failed: %s", result.error)
        await super().on_processing_complete(event, outcome)

    async def send(
        self, chat_id: str, content: str, reply_to: Optional[str] = None, metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        turn = replies.current()
        if turn is None:
            return await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)
        return await replies.settle_on_cancel(self._send_locked(chat_id, content, reply_to, metadata))

    async def _send_locked(self, chat_id, content, reply_to, metadata) -> SendResult:
        turn = replies.current()
        async with turn.lock:
            return await self._send_turn_reply(chat_id, content, reply_to, metadata)

    async def _send_turn_reply(self, chat_id, content, reply_to, metadata) -> SendResult:
        decision = replies.decide_send(metadata)
        final = bool((metadata or {}).get("notify"))
        if decision == "create" and not final:
            limit = int(getattr(self, "MAX_MESSAGE_LENGTH", 0) or 0)
            content = replies.clip_progress(self.format_message(content or ""), limit)
        if decision == "ignore":
            turn = replies.current()
            return SendResult(success=True, message_id=turn.message_id if turn else None)
        if decision == "edit":
            turn = replies.current()
            return await self._reuse_reply(
                chat_id, turn.message_id or "", content, reply_to, metadata, final=final,
            )
        result = await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)
        if getattr(result, "success", False) and getattr(result, "message_id", None):
            replies.note_created(str(result.message_id))
            replies.current().visible_text = content
            if final:
                replies.note_delivered()
        return result

    async def edit_message(
        self, chat_id: str, message_id: str, content: str, *, finalize: bool = False, metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        turn = replies.current()
        if turn is None or turn.message_id != message_id:
            return await super().edit_message(chat_id, message_id, content, finalize=finalize)
        return await replies.settle_on_cancel(self._edit_locked(chat_id, message_id, content, finalize))

    async def _edit_locked(self, chat_id, message_id, content, finalize) -> SendResult:
        turn = replies.current()
        async with turn.lock:
            if replies.decide_edit(message_id) == "ignore":
                return SendResult(success=True, message_id=message_id)
            # A segment boundary also carries finalize=True; only the lifecycle
            # hook knows that the whole turn has finished.
            if finalize:
                turn.final_preview = content
            return await self._edit_progress(chat_id, message_id, content)

    async def _edit_progress(self, chat_id, message_id, content) -> SendResult:
        turn = replies.current()
        limit = int(getattr(self, "MAX_MESSAGE_LENGTH", 0) or 0)
        content = replies.clip_progress(self.format_message(content or ""), limit)
        if content == turn.visible_text or turn.progress_edits >= replies.MAX_PROGRESS_EDITS:
            return SendResult(success=True, message_id=message_id)
        # Count attempts too: a lost acknowledgement may still consume an edit.
        turn.progress_edits += 1
        result = await super().edit_message(chat_id, message_id, content)
        if result.success:
            turn.visible_text = content
        return result

    async def _reuse_reply(
        self, chat_id: str, message_id: str, content: str, reply_to: Optional[str],
        metadata: Optional[dict[str, Any]], *, final: bool,
    ) -> SendResult:
        formatted = self.format_message(content or "")
        limit = int(getattr(self, "MAX_MESSAGE_LENGTH", 0) or 0)
        if not final:
            return await self._edit_progress(chat_id, message_id, formatted)
        turn = replies.current()
        chunks = replies.split_final(formatted, limit)
        if not chunks:
            return SendResult(success=False, error="Empty final reply")
        if turn.final_chunks and turn.final_chunks != chunks:
            return SendResult(success=False, error="Final reply changed during partial delivery")
        turn.final_chunks = chunks
        result = SendResult(success=True, message_id=message_id)
        if turn.next_final_chunk == 0:
            result = await super().edit_message(chat_id, message_id, chunks[0], finalize=True)
            if not getattr(result, "success", False):
                return result
            turn.next_final_chunk = 1
        for chunk in chunks[turn.next_final_chunk:]:
            extra = await super().send(chat_id, chunk, reply_to=reply_to, metadata=metadata)
            if not getattr(extra, "success", False):
                return extra
            turn.next_final_chunk += 1
        replies.note_delivered()
        if not getattr(result, "message_id", None):
            result.message_id = message_id
        return result

    async def _extract_message_content(self, message: Any):
        raw_type = getattr(message, "message_type", "") or ""
        raw_content = getattr(message, "content", "") or ""
        normalized = self._normalize(raw_type, raw_content, getattr(message, "mentions", None))
        expected = len(getattr(normalized, "image_keys", []) or []) + len(getattr(normalized, "media_refs", []) or [])
        text, inbound_type, media_urls, media_types, media_text_inlined, mentions = await super()._extract_message_content(message)
        note = replies.attachment_note(expected, len(media_urls))
        if note:
            text = f"{text}\n\n{note}" if text else note
        return text, inbound_type, media_urls, media_types, media_text_inlined, mentions

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
