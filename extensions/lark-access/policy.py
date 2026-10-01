"""Who may come in, who may change shared settings, and where ordinary files live.

The entry switch and the admin list are read from the protected runtime file.
Missing file means the switch is on and nobody is an admin yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
CLEANUP_HOUR = 4
CLEANUP_MINUTE = 22
MAX_AGE = timedelta(days=7)

REFUSAL_ADMIN = "只有管理员可以改技能、人设、长期记忆或模型。这件事已经拒绝。"
REFUSAL_LOCKED = "程序、管理员名单、进门开关、凭据和插件安装不能在对话里改。"
REFUSAL_AUTOMATIC = "普通对话不会自动记成技能或长期记忆。"

_WRITE_COMMANDS = {"learn", "refine"}
_ADMIN_COMMANDS = _WRITE_COMMANDS | {"model", "personality", "skills", "curator", "plugins"}
_SKILL_WRITES = {"create", "edit", "patch", "delete", "write_file", "remove_file"}
_MEMORY_WRITES = {"add", "replace", "remove", "batch"}
_CREDENTIAL_NAMES = {".env", "bot.env", "credentials.json", "auth.json"}
_LOCKED_DIRS = {"plugins", "pairing", "platforms"}


@dataclass(frozen=True)
class AccessConfig:
    auto_enroll: bool = True
    admins: tuple[str, ...] = ()


def load_config(path: Path) -> AccessConfig:
    if not path.is_file():
        return AccessConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AccessConfig()
    if not isinstance(data, dict):
        return AccessConfig()
    admins = tuple(
        item.strip()
        for item in data.get("admins") or []
        if isinstance(item, str) and item.strip()
    )
    return AccessConfig(auto_enroll=_as_bool(data.get("auto_enroll", True)), admins=admins)


def is_admin(config: AccessConfig, *user_ids: str, aliases: tuple[str, ...] = ()) -> bool:
    known = set(config.admins) | set(aliases)
    return any(user_id and user_id in known for user_id in user_ids)


def observe_aliases(config: AccessConfig, aliases: tuple[str, ...], observed: tuple[str, ...]) -> tuple[str, ...]:
    """Remember every id seen with an admin, so a later tool call can use the gateway's id."""
    observed_ids = tuple(dict.fromkeys(item for item in observed if item))
    if not observed_ids or not is_admin(config, *observed_ids, aliases=aliases):
        return aliases
    return tuple(dict.fromkeys((*aliases, *observed_ids)))


def load_aliases(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    raw = data.get("ids") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def save_aliases(path: Path, aliases: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ids": list(aliases)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def should_enroll(config: AccessConfig, *, platform: str, user_id: str, is_bot: bool) -> bool:
    return bool(config.auto_enroll and _is_feishu(platform) and user_id and not is_bot)


def inbound_text(text: str, *, is_admin_sender: bool) -> str | None:
    """Replacement for a gated slash command, or None when the message can pass through.

    A topic prefix sits before a blank line. Only the latest segment is the person's words.
    An allowed command is returned alone so the official command still runs.
    """
    segment = _latest_segment(text)
    command, args = _slash(segment)
    if command not in _ADMIN_COMMANDS:
        return None
    if _command_allowed(command, args, is_admin_sender):
        if command == "model" and args and not _model_scope(args):
            return f"/model {args} --global"
        return segment if segment == text.strip() else segment
    if command == "plugins" or _touches_locked_command(command, args):
        return REFUSAL_LOCKED
    return REFUSAL_ADMIN


def tool_decision(
    *,
    platform: str,
    user_ids: tuple[str, ...],
    tool_name: str,
    args: dict,
    config: AccessConfig,
    store: Path,
    background_review: bool,
    aliases: tuple[str, ...] = (),
) -> dict | None:
    """Block, relocate a write, or leave the tool alone. Empty when this turn is not from Lark."""
    if not _is_feishu(platform):
        return None
    admin = is_admin(config, *user_ids, aliases=aliases)
    if tool_name == "skill_manage" and _skill_write(args):
        if background_review:
            return _block(REFUSAL_AUTOMATIC)
        return None if admin else _block(REFUSAL_ADMIN)
    if tool_name == "memory" and _memory_write(args):
        if background_review:
            return _block(REFUSAL_AUTOMATIC)
        return None if admin else _block(REFUSAL_ADMIN)
    if tool_name == "terminal":
        return _terminal_decision(str(args.get("command") or ""), admin)
    if tool_name in {"write_file", "patch"}:
        return _file_decision(str(args.get("path") or ""), admin, store)
    return None


def turn_note(*, is_admin_sender: bool, store: Path) -> str:
    shared = f"对方要的文档、任务清单、图片和草稿只写到 {store}。"
    if is_admin_sender:
        return shared + "当前发送者是管理员，明确要求时可以记技能、改人设、改长期记忆、切换模型。不能改程序、名单、进门开关、凭据或安装插件。"
    return shared + "当前发送者不是管理员。要求改技能、人设、长期记忆、模型或插件时直接拒绝，不要去改。"


def cleanup_expired(root: Path, now: datetime, *, max_age: timedelta = MAX_AGE) -> dict[str, list[str]]:
    """Delete files last changed more than max_age ago. Newer files and their folders stay."""
    removed: list[str] = []
    kept: list[str] = []
    if not root.is_dir():
        return {"removed": removed, "kept": kept}
    cutoff = now.timestamp() - max_age.total_seconds()
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(relative)
        else:
            kept.append(relative)
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            continue
    return {"removed": removed, "kept": kept}


def cleanup_due(now: datetime, last_day: date | None) -> bool:
    local = now.astimezone(SHANGHAI)
    slot = local.replace(hour=CLEANUP_HOUR, minute=CLEANUP_MINUTE, second=0, microsecond=0)
    return local >= slot and last_day != local.date()


def next_cleanup_at(now: datetime) -> datetime:
    local = now.astimezone(SHANGHAI)
    slot = local.replace(hour=CLEANUP_HOUR, minute=CLEANUP_MINUTE, second=0, microsecond=0)
    if local >= slot:
        slot += timedelta(days=1)
    return slot


def read_cleanup_day(path: Path) -> date | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("date")
        return date.fromisoformat(raw) if isinstance(raw, str) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def write_cleanup_day(path: Path, day: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": day.isoformat()}), encoding="utf-8")


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _is_feishu(platform: str) -> bool:
    return str(platform or "").strip().lower() == "feishu"


def _latest_segment(text: str) -> str:
    parts = [part.strip() for part in str(text or "").split("\n\n") if part.strip()]
    return parts[-1] if parts else ""


def _slash(text: str) -> tuple[str, str]:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not line.startswith("/") or line.startswith("//"):
        return "", ""
    head, _, args = line[1:].partition(" ")
    name = head.split("@", 1)[0].strip().lower()
    if not name or "/" in name:
        return "", ""
    return name, args.strip()


def _model_scope(args: str) -> bool:
    tokens = set(args.split())
    return bool(tokens & {"--global", "--session", "--once"})


def _command_allowed(command: str, args: str, is_admin_sender: bool) -> bool:
    if command in _WRITE_COMMANDS or _touches_locked_command(command, args):
        return False if command == "plugins" or _touches_locked_command(command, args) else is_admin_sender
    if command == "plugins":
        return not args or args.split()[0].lower() in {"list", "status"}
    if command == "model":
        return is_admin_sender or not args
    if command in {"personality", "skills", "curator"}:
        if not args or args.split()[0].lower() in {"list", "search", "view", "status", "help"}:
            return True
        return is_admin_sender
    return True


def _touches_locked_command(command: str, args: str) -> bool:
    if command != "plugins":
        return False
    verb = args.split()[0].lower() if args else ""
    return verb in {"install", "remove", "uninstall", "enable", "disable", "update"}


def _skill_write(args: dict) -> bool:
    return str(args.get("action") or "").strip().lower() in _SKILL_WRITES


def _memory_write(args: dict) -> bool:
    action = str(args.get("action") or "").strip().lower()
    return action in _MEMORY_WRITES or not action


def _terminal_decision(command: str, admin: bool) -> dict | None:
    lowered = command.lower()
    if _terminal_locked(lowered):
        return _block(REFUSAL_LOCKED)
    if not admin and _terminal_shared(lowered):
        return _block(REFUSAL_ADMIN)
    return None


def _terminal_locked(command: str) -> bool:
    markers = (
        "hermes plugins",
        "plugin install",
        "pip install",
        "pip3 install",
        "lark-access/config.json",
        "bot.env",
        "/opt/hermes",
        "config.yaml",
    )
    if any(marker in command for marker in markers):
        return True
    return ".env" in command and any(verb in command for verb in (">", "tee ", "rm ", "mv ", "cp "))


def _terminal_shared(command: str) -> bool:
    markers = ("soul.md", "/skills/", " skills/", "/memories/", " memories/")
    writes = (">", "tee ", "rm ", "mv ", "cp ", "mkdir ")
    return any(marker in command for marker in markers) and any(verb in command for verb in writes)


def _file_decision(path: str, admin: bool, store: Path) -> dict | None:
    if not path.strip():
        return None
    if _locked_path(path):
        return _block(REFUSAL_LOCKED)
    if _shared_path(path):
        return None if admin else _block(REFUSAL_ADMIN)
    destination = _relocate(path, store)
    if destination is None:
        return None
    return {"action": "modify", "args": {"path": destination}}


def _locked_path(path: str) -> bool:
    parts = _parts(path)
    if not parts:
        return False
    name = parts[-1]
    if name in _CREDENTIAL_NAMES or name.endswith(".pem") or name == "config.yaml":
        return True
    if name in {"config.json", "aliases.json"} and "lark-access" in parts:
        return True
    if "opt" in parts and "hermes" in parts:
        return True
    return any(part in _LOCKED_DIRS for part in parts[:-1])


def _shared_path(path: str) -> bool:
    parts = _parts(path)
    if not parts:
        return False
    if parts[-1].lower() == "soul.md":
        return True
    return any(part in {"skills", "memories"} for part in parts[:-1])


def _relocate(path: str, store: Path) -> str | None:
    raw = path.replace("\\", "/").strip()
    store_text = store.as_posix().rstrip("/")
    if raw == store_text or raw.startswith(store_text + "/"):
        return None
    if raw.startswith("/workspace/"):
        relative = raw[len("/workspace/"):]
    elif raw.startswith("/"):
        relative = Path(raw).name
    else:
        relative = raw[2:] if raw.startswith("./") else raw
    safe = Path(relative)
    if any(part == ".." for part in safe.parts):
        safe = Path(safe.name)
    return (store / safe).as_posix()


def _parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part and part != "."]


def _block(message: str) -> dict:
    return {"action": "block", "message": message}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
