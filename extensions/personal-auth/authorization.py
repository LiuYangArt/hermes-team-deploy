"""Speaker-bound device authorization using the pinned official CLIs.

Pending credentials never become usable until the returned identity is checked.
Device codes stay in protected state, outside model-visible tool results.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .policy import has_lark_grant, has_meegle_grant, safe_id

SCHEMA = {
    "name": "personal_auth",
    "description": "普通成员管理自己的 Lark/Meegle 授权。start 返回链接、二维码和有效期；本人确认授权后 complete；status 查看；logout 仅退出本人。身份由当前 Lark 消息提供，不能指定其他人。Meegle 首次绑定先完成 Lark 授权核对邮箱。",
    "parameters": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "enum": ["lark", "meegle"]},
            "action": {"type": "string", "enum": ["start", "complete", "status", "logout"]},
            "host": {"type": "string", "enum": ["meegle.com", "project.feishu.cn"], "description": "仅 Meegle，默认 meegle.com"},
        },
        "required": ["service", "action"],
        "additionalProperties": False,
    },
}


class AuthError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind, self.message = kind, message


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False)
    temporary.replace(path)


def environment(root: Path, service: str, folder: Path) -> dict:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("LARKSUITE_CLI_", "MEEGLE_", "OPENCLAW_")) or key == "LARK_CHANNEL":
            env.pop(key, None)
    env.update(HERMES_HOME=str(root), HOME=str(folder), USER="hermes", MEEGLE_NO_UPDATE_CHECK="1",
               LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
    if service == "lark":
        env.update(LARKSUITE_CLI_CONFIG_DIR=str(folder), LARKSUITE_CLI_DATA_DIR=str(folder / ".data"))
    return env


def run_cli(argv: list[str], env: dict, cwd: Path, *, json_output=True, timeout=30) -> dict:
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise AuthError("pending", "这次还没有完成，请确认已在授权页面同意后再继续。") from None
    except OSError:
        raise AuthError("cli_unavailable", "服务器上的官方授权工具不可用，需要维护部署。") from None
    if result.returncode:
        # Never expose captured output: it may include credentials or device codes.
        try:
            error = json.loads(result.stderr or result.stdout).get("error", {})
            kind = str(error.get("subtype") or error.get("code") or "cli_failed")
        except (ValueError, AttributeError):
            kind = "cli_failed"
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", kind):
            kind = "cli_failed"
        raise AuthError(kind, "官方授权调用未成功，请检查应用配置、权限或网络；没有使用其他人的授权。")
    if not json_output:
        return {}
    try:
        value = json.loads(result.stdout)
        if isinstance(value, dict) and value.get("ok") is False:
            raise ValueError
        return value
    except ValueError:
        raise AuthError("invalid_response", "官方授权工具返回了无法识别的结果。") from None


def _data(value: dict) -> dict:
    for _ in range(3):
        if isinstance(value, dict) and isinstance(value.get("data"), dict):
            value = value["data"]
        else:
            break
    return value if isinstance(value, dict) else {}


class Authorization:
    def __init__(self, root: Path, runner=run_cli, now=time.time):
        self.root, self.run, self.now = root, runner, now

    def handle(self, speaker: str, service: str, action: str, host="meegle.com") -> dict:
        if not safe_id(speaker) or not speaker.startswith("ou_"):
            return {"ok": False, "error": "unknown_speaker", "message": "无法核实当前 Lark 发起人，不能绑定个人授权。"}
        if service not in ("lark", "meegle") or action not in ("start", "complete", "status", "logout") or host not in ("meegle.com", "project.feishu.cn"):
            return {"ok": False, "error": "invalid_action"}
        base = self.root / "personal-auth" / speaker
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        base.chmod(0o700)
        # A member can have multiple conversations; only one may change a flow at a time.
        with (base / "authorization.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return self._handle(base, speaker, service, action, host)
            except AuthError as error:
                return {"ok": False, "error": error.kind, "message": error.message}

    def _handle(self, base, speaker, service, action, host):
        pending_file = base / f"{service}-pending.json"
        pending = _read(pending_file)
        stage = base / f"{service}-pending"
        live = base / ("lark" if service == "lark" else "meegle-home")
        if pending and self.now() >= pending.get("expires_at", 0):
            self._clear_pending(pending_file, stage, pending)
            pending = {}
            if action == "complete":
                return {"ok": False, "error": "expired", "message": "授权链接已过期，请重新发起。"}
        if action == "logout":
            self._clear_pending(pending_file, stage, pending)
            shutil.rmtree(live, ignore_errors=True)
            return {"ok": True, "status": "logged_out", "message": "已清除你在机器人的这项登录。服务端授权可在对应平台的授权管理中撤销。"}
        if action == "status":
            valid = has_lark_grant(self.root, speaker) if service == "lark" else has_meegle_grant(self.root, speaker)
            return {"ok": True, "status": "authorized" if valid else "pending" if pending else "not_authorized",
                    "expires_at": pending.get("expires_at"), "message": "这是本机绑定状态；实际查询会由官方工具校验令牌。"}
        if action == "start":
            if pending:
                return self._public(pending)
            if service == "meegle":
                self._lark_identity(base / "lark", speaker, require_email=True)
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(mode=0o700)
            env = environment(self.root, service, stage)
            if service == "lark":
                self.run(["lark-cli", "config", "bind", "--source", "hermes", "--identity", "user-default"], env, stage, json_output=False)
                self.run(["lark-cli", "config", "strict-mode", "user"], env, stage, json_output=False)
                info = self.run(["lark-cli", "auth", "login", "--scope", "task:task:read contact:user.email:readonly", "--no-wait", "--json"], env, stage)
                url = info.get("verification_url")
            else:
                info = self.run(["meegle", "auth", "login", "--device-code", "--phase", "init", "--host", host, "--format", "json"], env, stage)
                url = info.get("verification_uri_complete")
            if not isinstance(url, str) or not url.startswith("https://") or not info.get("device_code"):
                raise AuthError("invalid_response", "授权服务没有返回有效入口。")
            pending = {"device_code": info["device_code"], "client_id": info.get("client_id"),
                       "url": url, "host": host, "expires_at": self.now() + int(info.get("expires_in", 600))}
            _write(pending_file, pending)
            # The official QR encoder needs no user token. Public artifact has no device code.
            output = self.root / "team-files" / "authorization"
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            filename = f"{uuid.uuid4().hex}.png"
            try:
                self.run(["lark-cli", "auth", "qrcode", url, "--output", filename], env, output, json_output=False)
                pending["qr_path"] = str(output / filename)
                _write(pending_file, pending)
            except AuthError:
                pending["qr_error"] = "二维码生成失败，请先使用授权链接。"
            return self._public(pending)
        if not pending:
            return {"ok": False, "error": "no_pending", "message": "你没有待完成的授权，请先发起。"}
        env = environment(self.root, service, stage)
        if service == "lark":
            # A timed-out CLI may already have persisted its token; verify before polling again.
            config = _read(stage / "hermes" / "config.json")
            if not any(app.get("users") for app in config.get("apps", [])):
                self.run(["lark-cli", "auth", "login", "--device-code", pending["device_code"], "--json"], env, stage)
            try:
                identity = self._lark_identity(stage, speaker)
            except AuthError as error:
                if error.kind == "identity_mismatch":
                    self._clear_pending(pending_file, stage, pending)
                raise
        else:
            if not (stage / ".meegle" / "credentials.enc").exists():
                result = self.run(["meegle", "auth", "login", "--device-code", "--phase", "poll", "--once", "--host", pending["host"], "--device-code-value", pending["device_code"], "--client-id", pending["client_id"], "--format", "json"], env, stage)
                if result.get("status") != "ok":
                    if result.get("status") == "expired_token":
                        self._clear_pending(pending_file, stage, pending)
                    return {"ok": False, "error": result.get("status", "pending"), "message": "尚未完成授权或链接已过期。"}
            expected = self._lark_identity(base / "lark", speaker, require_email=True)
            actual = _data(self.run(["meegle", "user", "me", "--format", "json"], env, stage))
            if not actual.get("user_key") or not actual.get("email") or actual["email"].strip().casefold() != expected["email"].strip().casefold():
                self._clear_pending(pending_file, stage, pending)
                raise AuthError("identity_mismatch", "扫码账号与当前 Lark 发起人不匹配，未保存授权。请用本人的同一工作邮箱账号重新授权。")
            identity = {"user_key": actual["user_key"], "email": actual["email"]}
        _write(stage / "verified.json", {"speaker": speaker, "verified_at": self.now(), **identity})
        old = base / f"{service}-previous"
        shutil.rmtree(old, ignore_errors=True)
        if live.exists():
            live.rename(old)
        try:
            stage.rename(live)
        except OSError:
            if old.exists():
                old.rename(live)
            raise
        shutil.rmtree(old, ignore_errors=True)
        self._clear_pending(pending_file, stage, pending)
        return {"ok": True, "status": "authorized", "message": "已核对为当前发起人的账号并保存。现在继续原来的查询。"}

    def _lark_identity(self, folder, speaker, require_email=False):
        if not (folder / "hermes" / "config.json").is_file():
            raise AuthError("lark_identity_required", "请先完成你自己的 Lark 授权，用于核实 Meegle 账号归属。")
        env = environment(self.root, "lark", folder)
        status = _data(self.run(["lark-cli", "auth", "status", "--json", "--verify"], env, folder))
        user = status.get("identities", {}).get("user", {})
        if user.get("openId") != speaker or not user.get("verified"):
            raise AuthError("identity_mismatch", "扫码账号与当前 Lark 发起人不匹配或授权未生效，未保存新授权。")
        info = _data(self.run(["lark-cli", "api", "GET", "/open-apis/authen/v1/user_info", "--as", "user"], env, folder))
        if info.get("open_id") != speaker:
            raise AuthError("identity_mismatch", "登录账号与当前发起人不匹配，未保存新授权。")
        email = info.get("email") or info.get("enterprise_email") or ""
        if require_email and not email:
            raise AuthError("email_required", "Lark 未返回工作邮箱，暂不能核对 Meegle 账号。请先补齐 Lark 邮箱读取权限并重新授权。")
        return {"open_id": speaker, "email": email}

    def _clear_pending(self, file, stage, pending):
        file.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
        qr = pending.get("qr_path")
        if qr:
            path = Path(qr)
            if path.parent == self.root / "team-files" / "authorization":
                path.unlink(missing_ok=True)

    def _public(self, pending):
        return {"ok": True, "status": "pending", "verification_url": pending["url"],
                "qr_path": pending.get("qr_path"), "expires_in": max(0, int(pending["expires_at"] - self.now())),
                "message": "请展示原样链接和二维码，让当前发起人本人授权。本轮先结束；本人回复已授权后调用 complete，然后继续原请求。"}
