"""Add the team plugins to a Hermes config without disturbing other settings."""

from __future__ import annotations

import sys
from pathlib import Path

TEAM_PLUGINS = ("lark-topics", "lark-access", "personal-auth")


def ensure_team_plugins(text: str, names: tuple[str, ...] = TEAM_PLUGINS) -> str:
    lines = text.splitlines()
    plugins_at = _top_level_key(lines, "plugins:")
    if plugins_at is None:
        block = ["plugins:", "  enabled:", *[f"    - {name}" for name in names]]
        body = text
        if body and not body.endswith("\n"):
            body += "\n"
        return body + "\n".join(block) + "\n"

    end = _section_end(lines, plugins_at)
    section = lines[plugins_at + 1:end]
    enabled_rel = _child_key(section, "enabled:")
    if enabled_rel is None:
        insert = ["  enabled:", *[f"    - {name}" for name in names]]
        lines[plugins_at + 1:plugins_at + 1] = insert
        return _join(lines)

    enabled_line = section[enabled_rel]
    inline = enabled_line.split("#", 1)[0].split(":", 1)[1].strip()
    if inline:
        present = _flow_names(inline)
        if present is None:
            raise ValueError("plugins.enabled must be a list")
        merged = list(dict.fromkeys([*present, *names]))
        indent = _indent(enabled_line)
        replacement = [" " * indent + "enabled:", *[" " * (indent + 2) + f"- {name}" for name in merged]]
        abs_at = plugins_at + 1 + enabled_rel
        lines[abs_at:abs_at + 1] = replacement
        return _join(lines)

    present, items_end = _block_items(section, enabled_rel)
    missing = [name for name in names if name not in present]
    if missing:
        indent = _indent(enabled_line) + 2
        added = [" " * indent + f"- {name}" for name in missing]
        abs_end = plugins_at + 1 + items_end
        lines[abs_end:abs_end] = added
    return _join(lines)


def _top_level_key(lines: list[str], key: str) -> int | None:
    for index, line in enumerate(lines):
        if line.startswith("#") or not line.strip():
            continue
        if _indent(line) == 0 and line.split("#", 1)[0].strip() == key:
            return index
    return None


def _section_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if _indent(raw) == 0:
            return index
    return len(lines)


def _child_key(section: list[str], key: str) -> int | None:
    for index, raw in enumerate(section):
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        head = raw.split("#", 1)[0].strip()
        if head == key or head.startswith(key):
            return index
    return None


def _block_items(section: list[str], enabled_rel: int) -> tuple[list[str], int]:
    base = _indent(section[enabled_rel])
    present: list[str] = []
    index = enabled_rel + 1
    while index < len(section):
        raw = section[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if _indent(raw) <= base:
            break
        body = raw.strip()
        if body.startswith("- "):
            present.append(body[2:].strip().strip("'\""))
        index += 1
    return present, index


def _flow_names(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _join(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def ensure_feishu_cron_toolset(text: str) -> str:
    return ensure_feishu_toolset(text, "cronjob")


def ensure_feishu_toolset(text: str, toolset: str) -> str:
    """Preserve the existing tools, adding the personal tool beside official defaults."""
    lines = text.splitlines()
    at = _top_level_key(lines, "platform_toolsets:")
    if at is None:
        if toolset == "personal_auth":
            return _join([*lines, "platform_toolsets:", "  feishu: [hermes-feishu, personal_auth]"])
        return text
    end = _section_end(lines, at)
    section = lines[at + 1:end]
    feishu_rel = _child_key(section, "feishu:")
    if feishu_rel is None:
        if toolset == "personal_auth":
            lines.insert(at + 1, "  feishu: [hermes-feishu, personal_auth]")
            return _join(lines)
        return text
    line = section[feishu_rel]
    comment = ""
    raw_value = line.split("#", 1)
    if len(raw_value) == 2:
        comment = " #" + raw_value[1].rstrip()
    inline = raw_value[0].split(":", 1)[1].strip()
    if inline:
        present = _flow_names(inline)
        if present is None or toolset in present:
            return text
        merged = ", ".join([*present, toolset])
        indent = " " * _indent(line)
        lines[at + 1 + feishu_rel] = f"{indent}feishu: [{merged}]{comment}"
        return _join(lines)
    present, items_end = _block_items(section, feishu_rel)
    if toolset in present:
        return text
    indent = _indent(line) + 2
    lines[at + 1 + items_end:at + 1 + items_end] = [" " * indent + "- " + toolset]
    return _join(lines)


def ensure_personal_cron_override(text: str) -> str:
    """Opt in to the managed creator-authorization wrapper, preserving unrelated config text."""
    lines = text.splitlines()
    start, end = 0, len(lines)
    for depth, key in enumerate(("plugins", "entries", "personal-auth", "allow_tool_override")):
        indent = depth * 2
        at = next((i for i in range(start, end) if _indent(lines[i]) == indent
                   and lines[i].strip().split(":", 1)[0] == key), None)
        if at is None:
            lines.insert(end, " " * indent + key + (": true" if depth == 3 else ":"))
            at = end
        elif depth == 3:
            lines[at] = " " * indent + key + ": true"
        elif lines[at].split(":", 1)[1].split("#", 1)[0].strip() not in ("", "{}"):
            raise ValueError(f"{key} must use a block mapping for managed tool configuration")
        elif "{}" in lines[at]:
            lines[at] = " " * indent + key + ":"
        start = at + 1
        end = next((i for i in range(start, len(lines)) if lines[i].strip()
                    and not lines[i].lstrip().startswith("#") and _indent(lines[i]) <= indent), len(lines))
    return _join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: enable_team_plugins.py CONFIG", file=sys.stderr)
        return 2
    path = Path(argv[1])
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = ensure_feishu_cron_toolset(ensure_team_plugins(text))
    path.write_text(ensure_personal_cron_override(ensure_feishu_toolset(updated, "personal_auth")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
