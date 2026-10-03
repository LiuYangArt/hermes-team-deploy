"""Lark entry and the three permission layers. Official transport stays in place."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .enroll import record_recognized
from .policy import (
    SHANGHAI,
    cleanup_due,
    cleanup_expired,
    inbound_text,
    is_admin,
    load_aliases,
    load_config,
    next_cleanup_at,
    observe_aliases,
    read_cleanup_day,
    save_aliases,
    should_enroll,
    tool_decision,
    turn_note,
    utc_now,
    write_cleanup_day,
)

logger = logging.getLogger(__name__)
_scheduler_lock = threading.Lock()
_scheduler_started = False


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_inbound)
    ctx.register_hook("pre_tool_call", _on_tool)
    ctx.register_hook("pre_llm_call", _on_turn)
    if os.environ.get("HERMES_LARK_ACCESS_SCHEDULER", "1") != "0":
        _start_scheduler()


async def _on_inbound(event=None, gateway=None, **_kwargs):
    source = getattr(event, "source", None)
    platform = _platform(source)
    user_id = str(getattr(source, "user_id", "") or "")
    observed = _sender_ids(event)
    config = load_config(_config_path())
    aliases = load_aliases(_alias_path())
    updated = observe_aliases(config, aliases, observed)
    if updated != aliases:
        save_aliases(_alias_path(), updated)
        aliases = updated
    admin = is_admin(config, *observed, aliases=aliases)
    if platform == "feishu" and not admin and gateway is not None:
        from tools.approval import has_blocking_approval
        command = event.get_command()
        pending = has_blocking_approval(gateway._session_key_for_source(source))
        plain_choice = gateway._plaintext_approval_words().get(str(event.text or "").strip().lower()) if pending else None
        if command in {"approve", "deny"} or plain_choice is not None:
            adapter = gateway._delivery_adapter_for(source)
            if adapter is not None:
                await adapter.send(
                    source.chat_id, "危险命令的批准或拒绝只能由管理员处理。查询自己的任务和个人扫码授权不需要这类批准。",
                    reply_to=getattr(event, "message_id", None),
                    metadata={"thread_id": source.thread_id} if source.thread_id else None,
                )
            return {"action": "skip", "reason": "execution_approval_requires_admin"}
    if should_enroll(config, platform=platform, user_id=user_id, is_bot=bool(getattr(source, "is_bot", False))):
        try:
            record_recognized(user_id, str(getattr(source, "user_name", "") or ""))
        except Exception:
            logger.warning("could not record a colleague in the recognized list", exc_info=True)
    replacement = inbound_text(
        str(getattr(event, "text", "") or ""),
        is_admin_sender=admin,
    )
    if replacement is None:
        return None
    return {"action": "rewrite", "text": replacement}


def _on_tool(tool_name="", args=None, **_kwargs):
    platform, user_id, alt_id = _session_identity()
    return tool_decision(
        platform=platform,
        user_ids=(user_id, alt_id),
        tool_name=str(tool_name or ""),
        args=args if isinstance(args, dict) else {},
        config=load_config(_config_path()),
        store=_store(),
        background_review=_is_background_review(),
        aliases=load_aliases(_alias_path()),
    )


def _on_turn(platform="", sender_id="", **_kwargs):
    if str(platform or "").strip().lower() != "feishu":
        return None
    config = load_config(_config_path())
    aliases = load_aliases(_alias_path())
    return {
        "context": turn_note(
            is_admin_sender=is_admin(config, str(sender_id or ""), aliases=aliases),
            store=_store(),
        )
    }


def _home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _config_path() -> Path:
    return _home() / "lark-access" / "config.json"


def _alias_path() -> Path:
    return _home() / "lark-access" / "aliases.json"


def _store() -> Path:
    return _home() / "team-files"


def _stamp_path() -> Path:
    return _home() / "lark-access" / "last-cleanup.json"


def _platform(source) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", None) or platform or "")


def _sender_ids(event) -> tuple[str, ...]:
    source = getattr(event, "source", None)
    found = [str(value) for value in (getattr(source, "user_id", None), getattr(source, "user_id_alt", None)) if value]
    sender = _dig(getattr(event, "raw_message", None), ("event", "sender", "sender_id"))
    for key in ("open_id", "user_id", "union_id"):
        value = _field(sender, key)
        if value:
            found.append(str(value))
    return tuple(dict.fromkeys(found))


def _dig(value, path):
    current = value
    for key in path:
        current = _field(current, key)
        if current is None:
            return None
    return current


def _field(value, key):
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _session_identity() -> tuple[str, str, str]:
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return "", "", ""
    return (
        get_session_env("HERMES_SESSION_PLATFORM", ""),
        get_session_env("HERMES_SESSION_USER_ID", ""),
        get_session_env("HERMES_SESSION_USER_ID_ALT", ""),
    )


def _is_background_review() -> bool:
    try:
        from tools.skill_provenance import is_background_review
    except Exception:
        return False
    return bool(is_background_review())


def _start_scheduler() -> None:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        threading.Thread(target=_cleanup_loop, name="lark-access-cleanup", daemon=True).start()
        _scheduler_started = True


def _cleanup_loop() -> None:
    while True:
        try:
            _cleanup_once()
        except Exception:
            logger.warning("team file cleanup failed", exc_info=True)
        delay = max(1.0, (next_cleanup_at(utc_now()) - utc_now()).total_seconds())
        time.sleep(delay)


def _cleanup_once() -> None:
    now = utc_now()
    stamp = _stamp_path()
    if not cleanup_due(now, read_cleanup_day(stamp)):
        return
    cleanup_expired(_store(), now)
    write_cleanup_day(stamp, now.astimezone(SHANGHAI).date())
