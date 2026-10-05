"""Stable Lark discussion keys, decided before the official batch and send paths.

A first group @ has no server thread yet. The session must already use one key,
and later server thread ids must resolve back to that same key. Local message
ids are never treated as server thread ids.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class TopicStoreError(RuntimeError):
    """The topic map cannot be trusted, so the adapter must not keep running half-applied."""


@dataclass(frozen=True)
class Topic:
    app_id: str
    chat_id: str
    discussion_key: str
    anchor_message_id: str
    server_thread_id: Optional[str]
    root_id: Optional[str]
    participants: frozenset[str]
    attention: str = "talking"
    active_sender: Optional[str] = None
    heard: tuple[str, ...] = ()


@dataclass(frozen=True)
class InboundDecision:
    """How one admitted group message should be keyed. ``session_thread_id`` is
    what the session uses; ``None`` leaves the official thread id untouched."""

    session_thread_id: Optional[str]
    discussion_key: Optional[str]
    created: bool
    deliver: bool = True
    context: Optional[str] = None


@dataclass(frozen=True)
class OutboundPlan:
    """``official`` keeps the stock send. ``reply_in_thread`` must reply to an
    anchor and must not create a top-level message. ``server_thread`` replies
    to the topic's own anchor, never to a main-chat quote. ``fail`` refuses
    the send."""

    mode: str
    thread_id: Optional[str]
    reply_to: Optional[str]
    discussion_key: Optional[str]
    error: Optional[str]


def _clean(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def is_server_thread_id(value: Optional[str]) -> bool:
    """Lark native topic ids use this prefix. Local message ids must not pass."""
    return bool(value) and str(value).startswith("omt_")


def sender_is_this_bot(
    sender_id: Optional[str], app_id: Optional[str], bot_open_id: Optional[str] = None,
) -> bool:
    """A fetched Lark message reports the app id for this bot's own sends."""
    sender = _clean(sender_id)
    if not sender:
        return False
    return sender in {_clean(app_id), _clean(bot_open_id)} - {None}


def sender_id_from_fetched_message(message: Any) -> Optional[str]:
    if message is None:
        return None
    sender = message.get("sender") if isinstance(message, dict) else getattr(message, "sender", None)
    if isinstance(sender, dict):
        return _clean(sender.get("id"))
    return _clean(getattr(sender, "id", None))


def sender_id_from_get_response(response: Any) -> Optional[str]:
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    items = _message_items(data)
    if items:
        return sender_id_from_fetched_message(items[0])
    return sender_id_from_fetched_message(data)


class TopicStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._topics: dict[str, Topic] = {}
        self._aliases: dict[str, str] = {}
        self._unrelated: set[str] = set()
        self._load()

    def topic_for_alias(self, app_id: str, chat_id: str, alias: Optional[str]) -> Optional[Topic]:
        alias = _clean(alias)
        if not app_id or not chat_id or not alias:
            return None
        with self._lock:
            return self._topic_for_alias(app_id, chat_id, alias)

    def allows_unmentioned(
        self, *, app_id: str, chat_id: str, thread_id: Optional[str],
        parent_id: Optional[str], root_id: Optional[str], sender_id: Optional[str],
    ) -> bool:
        """Let a known topic's messages through the mention gate.

        Messages already inside the topic are kept even when the sender has not
        @ the bot, so they can be remembered. A main-chat quote still needs the
        sender to already be part of that topic.
        """
        sender = _clean(sender_id)
        if _clean(thread_id) and self.topic_for_alias(app_id, chat_id, thread_id) is not None:
            return True
        if not sender:
            return False
        topic = self.topic_for_alias(app_id, chat_id, parent_id) or self.topic_for_alias(
            app_id, chat_id, root_id)
        return topic is not None and (sender in topic.participants or sender == topic.active_sender)

    def needs_origin_check(self, app_id: str, chat_id: str, thread_id: Optional[str]) -> bool:
        """A server topic we have not opened and have not already classified.

        The first message has to be seen once, so we can tell whether the topic
        started from this bot. After that, unrelated topics stay behind the mention gate.
        """
        thread_id = _clean(thread_id)
        if not app_id or not chat_id or not is_server_thread_id(thread_id):
            return False
        with self._lock:
            if self._topic_for_alias(app_id, chat_id, thread_id) is not None:
                return False
            return self._alias_key(app_id, chat_id, thread_id) not in self._unrelated

    def sender_is_active(self, app_id: str, chat_id: str, discussion_key: Optional[str], sender_id: Optional[str]) -> bool:
        sender = _clean(sender_id)
        topic = self.topic_for_alias(app_id, chat_id, discussion_key)
        return bool(sender and topic and topic.active_sender == sender)

    def claim_speaker(self, app_id: str, chat_id: str, discussion_key: Optional[str], sender_id: Optional[str]) -> None:
        """The queued request has actually started. This person is now who the bot is answering."""
        sender = _clean(sender_id)
        if not sender:
            return
        with self._lock:
            topic = self._topic_for_alias(app_id, chat_id, discussion_key)
            if topic is None:
                return
            participants = topic.participants if sender in topic.participants else frozenset({*topic.participants, sender})
            self._replace(topic, attention="talking", active_sender=sender, participants=participants)
            self._save()

    def route_inbound(
        self, *, app_id: str, chat_id: str, chat_type: str, message_id: str,
        thread_id: Optional[str], parent_id: Optional[str], root_id: Optional[str],
        sender_id: Optional[str], mentioned: bool, is_bot: bool = False,
        mentions_other: bool = False, text: str = "", root_from_self: Optional[bool] = None,
    ) -> InboundDecision:
        message_id = _clean(message_id) or ""
        sender = _clean(sender_id)
        native_thread = _clean(thread_id)
        if not app_id or not chat_id or not message_id or _is_direct(chat_type):
            return InboundDecision(None, None, False)

        with self._lock:
            if native_thread:
                topic = self._topic_for_alias(app_id, chat_id, native_thread)
                if topic is not None:
                    return self._decide(topic, sender, message_id, text, mentioned, mentions_other)
                if is_server_thread_id(native_thread):
                    return self._route_unknown_server_thread(
                        app_id=app_id, chat_id=chat_id, native_thread=native_thread,
                        root_id=_clean(root_id), parent_id=_clean(parent_id), message_id=message_id,
                        sender=sender, text=text, mentioned=mentioned, is_bot=is_bot,
                        mentions_other=mentions_other, root_from_self=root_from_self,
                    )
                return InboundDecision(None, None, False)

            quoted = self._topic_for_alias(app_id, chat_id, parent_id) or self._topic_for_alias(
                app_id, chat_id, root_id)
            if quoted is not None and (
                mentioned or (sender and (sender in quoted.participants or sender == quoted.active_sender))
            ):
                return self._decide(quoted, sender, message_id, text, mentioned, mentions_other)

            if not mentioned or is_bot:
                return InboundDecision(None, None, False)

            existing = self._topic_for_alias(app_id, chat_id, message_id)
            if existing is not None:
                return self._decide(existing, sender, message_id, text, True, mentions_other)

            topic = Topic(
                app_id=app_id, chat_id=chat_id, discussion_key=message_id,
                anchor_message_id=message_id, server_thread_id=None, root_id=None,
                participants=frozenset({sender} if sender else ()),
                attention="talking", active_sender=sender, heard=(),
            )
            self._topics[self._topic_storage_key(topic)] = topic
            self._aliases[self._alias_key(app_id, chat_id, message_id)] = message_id
            self._save()
            return InboundDecision(message_id, message_id, True)

    def _route_unknown_server_thread(
        self, *, app_id: str, chat_id: str, native_thread: str, root_id: Optional[str],
        parent_id: Optional[str], message_id: str, sender: Optional[str], text: str,
        mentioned: bool, is_bot: bool, mentions_other: bool, root_from_self: Optional[bool],
    ) -> InboundDecision:
        """A topic Lark already has, which this extension has not joined.

        @ the bot adopts it. A topic that started from this bot's own message
        adopts the first person who speaks, unless that first line calls someone else.
        A colleague's topic is remembered so later lines are not looked up again.
        """
        if sender and not is_bot and (mentioned or root_from_self is True):
            return self._adopt_server_thread(
                app_id=app_id, chat_id=chat_id, native_thread=native_thread, root_id=root_id,
                parent_id=parent_id, message_id=message_id, sender=sender, text=text,
                mentioned=mentioned, mentions_other=mentions_other,
            )
        if root_from_self is False:
            self._unrelated.add(self._alias_key(app_id, chat_id, native_thread))
            self._save()
        return InboundDecision(None, None, False, False)

    def _adopt_server_thread(
        self, *, app_id: str, chat_id: str, native_thread: str, root_id: Optional[str],
        parent_id: Optional[str], message_id: str, sender: str, text: str,
        mentioned: bool, mentions_other: bool,
    ) -> InboundDecision:
        self._unrelated.discard(self._alias_key(app_id, chat_id, native_thread))
        reply_anchor = root_id or parent_id or message_id
        discussion_key = reply_anchor
        existing = self._topic_for_alias(app_id, chat_id, reply_anchor)
        if existing is not None and existing.server_thread_id not in (None, native_thread):
            # The anchor already belongs to a different topic. Do not steal it.
            existing = None
            discussion_key = message_id
        elif existing is not None and not existing.server_thread_id:
            existing = self._replace(
                existing, server_thread_id=native_thread, root_id=root_id or existing.root_id,
            )
        if existing is not None:
            self._bind_alias(app_id, chat_id, native_thread, existing.discussion_key)
            self._save()
            return self._decide(existing, sender, message_id, text, mentioned, mentions_other)

        quiet = (not mentioned) and mentions_other
        line = (text or "").strip()
        heard = (f"{sender or '有人'}: {line[:200]}",) if quiet and line else ()
        topic = Topic(
            app_id=app_id, chat_id=chat_id, discussion_key=discussion_key,
            anchor_message_id=reply_anchor, server_thread_id=native_thread, root_id=root_id,
            participants=frozenset({sender}),
            attention="listening" if quiet else "talking", active_sender=sender, heard=heard,
        )
        self._topics[self._topic_storage_key(topic)] = topic
        for alias in (native_thread, discussion_key, message_id, root_id):
            self._bind_alias(app_id, chat_id, alias, discussion_key)
        self._save()
        return InboundDecision(discussion_key, discussion_key, True, not quiet)

    def _bind_alias(self, app_id: str, chat_id: str, alias: Optional[str], discussion_key: str) -> None:
        alias = _clean(alias)
        if not alias:
            return
        key = self._alias_key(app_id, chat_id, alias)
        current = self._aliases.get(key)
        if current and current != discussion_key:
            return
        self._aliases[key] = discussion_key

    def _decide(
        self, topic: Topic, sender: Optional[str], message_id: str, text: str,
        mentions_bot: bool, mentions_other: bool,
    ) -> InboundDecision:
        """Who the bot answers. @ the bot wakes it. The current speaker @ing someone else quiets it."""
        if mentions_bot:
            topic = self._remember(topic, sender, message_id, add_participant=True)
            context = self._take_context(topic)
            return InboundDecision(topic.discussion_key, topic.discussion_key, False, True, context)

        if topic.attention == "listening" or sender != topic.active_sender:
            self._remember(topic, sender, message_id, add_participant=False, heard_text=text)
            return InboundDecision(topic.discussion_key, topic.discussion_key, False, False, None)

        if mentions_other:
            quiet = self._replace(topic, attention="listening")
            self._remember(quiet, sender, message_id, add_participant=False, heard_text=text)
            return InboundDecision(topic.discussion_key, topic.discussion_key, False, False, None)

        topic = self._remember(topic, sender, message_id, add_participant=False)
        context = self._take_context(topic)
        return InboundDecision(topic.discussion_key, topic.discussion_key, False, True, context)

    def plan_outbound(
        self, *, app_id: str, chat_id: str, metadata_thread_id: Optional[str], reply_to: Optional[str],
    ) -> OutboundPlan:
        token = _clean(metadata_thread_id)
        topic = self.topic_for_alias(app_id, chat_id, token)
        if topic is None:
            return OutboundPlan("official", token, _clean(reply_to), None, None)
        if is_server_thread_id(topic.server_thread_id):
            # Replying to the main-chat quote opens a second topic. Posting with
            # the server id as the receive target is rejected by Lark. Reply to
            # the anchor that already belongs to this topic instead.
            anchor = topic.anchor_message_id
            if not anchor:
                return OutboundPlan("fail", None, None, topic.discussion_key, "missing topic anchor")
            return OutboundPlan(
                "server_thread", topic.server_thread_id, anchor, topic.discussion_key, None)
        anchor = _clean(reply_to) or topic.anchor_message_id
        if not anchor:
            return OutboundPlan("fail", None, None, topic.discussion_key, "missing topic anchor")
        # The discussion key stays only as a truthy flag beside a real reply target.
        return OutboundPlan("reply_in_thread", topic.discussion_key, anchor, topic.discussion_key, None)

    def bind_server_topic(
        self, *, app_id: str, chat_id: str, discussion_key: str,
        message_id: Optional[str], thread_id: Optional[str], root_id: Optional[str],
    ) -> bool:
        """Record the server topic after a successful reply. Never replace a different topic."""
        discussion_key = _clean(discussion_key) or ""
        thread_id = _clean(thread_id)
        if not is_server_thread_id(thread_id):
            return False
        with self._lock:
            topic = self._topic_for_alias(app_id, chat_id, discussion_key)
            if topic is None:
                return False
            if topic.server_thread_id and topic.server_thread_id != thread_id:
                return False
            root = _clean(root_id) or topic.root_id
            updated = self._replace(topic, server_thread_id=thread_id, root_id=root)
            self._aliases[self._alias_key(app_id, chat_id, thread_id)] = updated.discussion_key
            if root:
                self._aliases[self._alias_key(app_id, chat_id, root)] = updated.discussion_key
            sent = _clean(message_id)
            if sent:
                self._aliases[self._alias_key(app_id, chat_id, sent)] = updated.discussion_key
            self._save()
            return True

    def _replace(self, topic: Topic, **changes: Any) -> Topic:
        current = dict(
            app_id=topic.app_id, chat_id=topic.chat_id, discussion_key=topic.discussion_key,
            anchor_message_id=topic.anchor_message_id, server_thread_id=topic.server_thread_id,
            root_id=topic.root_id, participants=topic.participants, attention=topic.attention,
            active_sender=topic.active_sender, heard=topic.heard,
        )
        current.update(changes)
        updated = Topic(**current)
        self._topics[self._topic_storage_key(updated)] = updated
        return updated

    def _remember(
        self, topic: Topic, sender: Optional[str], message_id: str, *,
        add_participant: bool, heard_text: Optional[str] = None,
    ) -> Topic:
        participants = topic.participants
        if add_participant and sender and sender not in participants:
            participants = frozenset({*participants, sender})
        heard = topic.heard
        line = (heard_text or "").strip()
        if line:
            heard = (*heard, f"{sender or '有人'}: {line[:200]}")[-20:]
        if participants != topic.participants or heard != topic.heard:
            topic = self._replace(topic, participants=participants, heard=heard)
        if message_id:
            self._aliases[self._alias_key(topic.app_id, topic.chat_id, message_id)] = topic.discussion_key
        self._save()
        return topic

    def _take_context(self, topic: Topic) -> Optional[str]:
        current = self._topics.get(self._topic_storage_key(topic)) or topic
        if not current.heard:
            return None
        context = "话题里刚才还有这些话，仅作背景，不要逐条回复：\n" + "\n".join(current.heard)
        self._replace(current, heard=())
        self._save()
        return context

    def _topic_for_alias(self, app_id: str, chat_id: str, alias: Optional[str]) -> Optional[Topic]:
        alias = _clean(alias)
        if not alias:
            return None
        key = self._aliases.get(self._alias_key(app_id, chat_id, alias))
        if key is None and self._topic_storage_key_raw(app_id, chat_id, alias) in self._topics:
            key = alias
        if key is None:
            return None
        return self._topics.get(self._topic_storage_key_raw(app_id, chat_id, key))

    @staticmethod
    def _alias_key(app_id: str, chat_id: str, alias: str) -> str:
        return f"{app_id}\n{chat_id}\n{alias}"

    @staticmethod
    def _topic_storage_key_raw(app_id: str, chat_id: str, discussion_key: str) -> str:
        return f"{app_id}\n{chat_id}\n{discussion_key}"

    def _topic_storage_key(self, topic: Topic) -> str:
        return self._topic_storage_key_raw(topic.app_id, topic.chat_id, topic.discussion_key)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TopicStoreError(f"cannot read topic map: {self.path}") from exc
        topics = payload.get("topics") if isinstance(payload, dict) else None
        aliases = payload.get("aliases") if isinstance(payload, dict) else None
        unrelated = payload.get("unrelated_threads", []) if isinstance(payload, dict) else None
        if not isinstance(topics, list) or not isinstance(aliases, dict) or not isinstance(unrelated, list):
            raise TopicStoreError(f"topic map is not usable: {self.path}")
        loaded: dict[str, Topic] = {}
        for item in topics:
            topic = _topic_from_json(item)
            if topic is None:
                raise TopicStoreError(f"topic map has an invalid row: {self.path}")
            loaded[self._topic_storage_key(topic)] = topic
        cleaned_aliases: dict[str, str] = {}
        for alias, key in aliases.items():
            if not isinstance(alias, str) or not isinstance(key, str) or not key:
                raise TopicStoreError(f"topic map has an invalid alias: {self.path}")
            cleaned_aliases[alias] = key
        cleaned_unrelated: set[str] = set()
        for item in unrelated:
            if not isinstance(item, str) or not item:
                raise TopicStoreError(f"topic map has an invalid unrelated topic: {self.path}")
            cleaned_unrelated.add(item)
        self._topics = loaded
        self._aliases = cleaned_aliases
        self._unrelated = cleaned_unrelated

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "topics": [_topic_to_json(topic) for topic in self._topics.values()],
            "aliases": self._aliases,
            "unrelated_threads": sorted(self._unrelated),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)


def _message_items(data: Any) -> Optional[list]:
    """A Lark payload's message list. Dicts also have an ``items`` method, so that does not count."""
    if isinstance(data, dict):
        items = data.get("items")
    else:
        items = getattr(data, "items", None)
    return items if isinstance(items, list) and items else None


def thread_fields_from_response(response: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Read message, thread, and root ids from a Lark send response without guessing."""
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    items = _message_items(data)
    if items:
        data = items[0]

    def _field(name: str) -> Optional[str]:
        if data is None:
            return None
        if isinstance(data, dict):
            return _clean(data.get(name))
        return _clean(getattr(data, name, None))

    return _field("message_id"), _field("thread_id"), _field("root_id")


def _is_direct(chat_type: str) -> bool:
    return (chat_type or "").lower() in {"p2p", "dm", "private"}


def _topic_from_json(item: Any) -> Optional[Topic]:
    if not isinstance(item, dict):
        return None
    app_id = _clean(item.get("app_id"))
    chat_id = _clean(item.get("chat_id"))
    discussion_key = _clean(item.get("discussion_key"))
    anchor = _clean(item.get("anchor_message_id"))
    if not app_id or not chat_id or not discussion_key or not anchor:
        return None
    participants = item.get("participants") or []
    if not isinstance(participants, list):
        return None
    parts = [part for part in (_clean(part) for part in participants) if part]
    active = _clean(item.get("active_sender"))
    if not active and len(parts) == 1:
        active = parts[0]
    attention = item.get("attention") if item.get("attention") in {"talking", "listening"} else "talking"
    raw_heard = item.get("heard") or []
    heard = [line for line in raw_heard if isinstance(line, str) and line.strip()] if isinstance(raw_heard, list) else []
    return Topic(
        app_id=app_id, chat_id=chat_id, discussion_key=discussion_key, anchor_message_id=anchor,
        server_thread_id=_clean(item.get("server_thread_id")), root_id=_clean(item.get("root_id")),
        participants=frozenset(parts),
        attention=attention, active_sender=active, heard=tuple(heard),
    )


def _topic_to_json(topic: Topic) -> dict[str, Any]:
    return {
        "app_id": topic.app_id,
        "chat_id": topic.chat_id,
        "discussion_key": topic.discussion_key,
        "anchor_message_id": topic.anchor_message_id,
        "server_thread_id": topic.server_thread_id,
        "root_id": topic.root_id,
        "participants": sorted(topic.participants),
        "attention": topic.attention,
        "active_sender": topic.active_sender,
        "heard": list(topic.heard),
    }
