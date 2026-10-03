"""Per-person Lark task reads and Meegle. The shared bot Lark config stays untouched."""

from __future__ import annotations

import logging
import json
from pathlib import Path

from .policy import (
    TURN_NOTE,
    canonical,
    load_links,
    observe,
    retire_shared_meegle,
    save_links,
    tool_decision,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    from .authorization import SCHEMA
    ctx.register_tool(name="personal_auth", toolset="personal_auth", schema=SCHEMA, handler=_authorize)
    try:
        outcome = retire_shared_meegle(_home())
        if outcome == "moved":
            logger.info("retired the shared Meegle login so it is not used for anyone")
    except Exception:
        logger.warning("could not retire the shared Meegle login", exc_info=True)
    ctx.register_hook("pre_gateway_dispatch", _on_inbound)
    ctx.register_hook("pre_tool_call", _on_tool)
    ctx.register_hook("pre_llm_call", _on_turn)


def _authorize(args):
    from .authorization import Authorization
    platform, user_id, alt_id = _session_identity()
    if platform != "feishu":
        return json.dumps({"ok": False, "error": "lark_request_required"})
    speaker = canonical((user_id, alt_id), load_links(_links_path()))
    result = Authorization(_home()).handle(speaker, args.get("service"), args.get("action"), args.get("host", "meegle.com"))
    return json.dumps(result, ensure_ascii=False)


def _on_inbound(event=None, **_kwargs):
    source = getattr(event, "source", None)
    platform = _platform(source)
    if platform != "feishu":
        return None
    ids = _sender_ids(event)
    path = _links_path()
    current = load_links(path)
    updated = observe(current, ids)
    if updated != current:
        save_links(path, updated)
    return None


def _on_tool(tool_name="", args=None, **_kwargs):
    _platform_name, user_id, alt_id = _session_identity()
    if _platform_name != "feishu":
        return None
    decision = tool_decision(
        user_ids=(user_id, alt_id),
        tool_name=str(tool_name or ""),
        args=args if isinstance(args, dict) else {},
        root=_home(),
        links=load_links(_links_path()),
    )
    if not decision:
        return None
    for raw in decision.pop("ensure_dirs", ()):
        _ensure(Path(raw))
    return decision


def _on_turn(platform="", **_kwargs):
    if str(platform or "").strip().lower() != "feishu":
        return None
    return {"context": TURN_NOTE}


def _home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _links_path() -> Path:
    return _home() / "personal-auth" / "speakers.json"


def _ensure(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _platform(source) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", None) or platform or "").strip().lower()


def _sender_ids(event) -> tuple[str, ...]:
    source = getattr(event, "source", None)
    found = [str(value) for value in (getattr(source, "user_id", None), getattr(source, "user_id_alt", None)) if value]
    sender = _dig(getattr(event, "raw_message", None), ("event", "sender", "sender_id"))
    for key in ("open_id", "user_id", "union_id"):
        value = _field(sender, key)
        if value:
            found.append(str(value))
    return tuple(dict.fromkeys(found))


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
