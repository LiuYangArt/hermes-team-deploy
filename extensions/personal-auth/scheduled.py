"""Resolve unattended calls from the scheduler's internal task id, never model arguments."""
from __future__ import annotations

import json
import re
from .policy import has_lark_grant, has_meegle_grant


def register_cron(ctx):
    from copy import deepcopy
    from tools.cronjob_tools import CRONJOB_SCHEMA, check_cronjob_requirements
    schema = deepcopy(CRONJOB_SCHEMA)
    schema["parameters"]["properties"]["personal_services"] = {
        "type": "array", "items": {"type": "string", "enum": ["lark", "meegle"]},
        "description": "Lark 创建任务时必须列出所需个人授权：Meegle 操作为 meegle，查询本人 Lark 任务为 lark；无需个人授权填 []。创建前必须完成本人授权。",
    }
    ctx.register_tool(name="cronjob_manage", toolset="cronjob", schema=schema,
                      handler=manage_cron, check_fn=check_cronjob_requirements, override=True)


def manage_cron(args, **kwargs):
    from tools.cronjob_tools import _cronjob_handler, _notify_provider_jobs_changed_safe
    from cron.jobs import update_job, resume_job, resolve_job_ref
    from . import _session_identity, _home, _links_path
    from .policy import canonical, load_links
    if str(kwargs.get("task_id", "")).startswith("cron:") and args.get("action") != "list":
        return json.dumps({"success": False, "error": "定时任务管理须由管理员在 Lark 对话中操作。"}, ensure_ascii=False)
    platform, user, alt = _session_identity(kwargs.get("task_id", ""))
    if platform != "feishu":
        return _cronjob_handler(args, **kwargs)
    speaker = canonical((user, alt), load_links(_links_path()))
    if args.get("action") != "list":
        try:
            config = json.loads((_home() / "lark-access" / "config.json").read_text())
            admin = speaker in config.get("admins", [])
        except (OSError, ValueError, TypeError):
            admin = False
        if not admin:
            return json.dumps({"success": False, "error": "只有管理员可以创建或管理定时任务。"}, ensure_ascii=False)
    gate = creation_gate(args, speaker, _home())
    if gate:
        return json.dumps({"success": False, "error": gate["message"]}, ensure_ascii=False)
    action = args.get("action")
    if action == "create":
        # Never expose a runnable job before its authorization requirements are saved.
        response = json.loads(_cronjob_handler({**args, "paused": True,
                              "paused_reason": "正在保存创建者授权要求"}, **kwargs))
        if not response.get("success"):
            return json.dumps(response, ensure_ascii=False)
        ident = response["job_id"]
        update_job(ident, {"personal_services": required_services(args), "personal_creator": speaker,
                          "paused_reason": args.get("paused_reason")})
        if not args.get("paused", False):
            resume_job(ident)
            _notify_provider_jobs_changed_safe()
        from tools.cronjob_job_args import _format_job
        job = resolve_job_ref(ident)
        response.update(job=_format_job(job), next_run_at=job.get("next_run_at"),
                        message="定时任务已创建，固定使用创建者授权。", personal_services=job["personal_services"])
        return json.dumps(response, ensure_ascii=False)
    if action == "update":
        job = resolve_job_ref(args.get("job_id") or "")
        if job:
            needed = required_services({**job, **args})
            if set(needed) != set(job.get("personal_services", [])):
                return json.dumps({"success": False, "error": "更换个人授权服务需由创建者重新创建任务；现有任务的授权范围保持固定。"}, ensure_ascii=False)
    return _cronjob_handler(args, **kwargs)


def creator_identity(task_id):
    from cron.jobs import get_job
    parts = str(task_id).split(":")
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return "feishu", "", ""
    job = get_job(parts[1]) or {}
    origin = job.get("origin") or {}
    if not isinstance(origin, dict) or origin.get("platform") != "feishu":
        return "feishu", "", ""
    return "feishu", str(job.get("personal_creator") or ""), ""


def runtime_gate(task_id, tool_name, args):
    from cron.jobs import get_job
    from .policy import _owns_tasks, _uses_meegle
    parts = str(task_id).split(":")
    job = get_job(parts[1]) if len(parts) == 3 else None
    services = (job or {}).get("personal_services", [])
    service = args.get("service") if tool_name == "personal_auth" else None
    if tool_name == "terminal":
        command = str(args.get("command") or "")
        service = "meegle" if _uses_meegle(command) else "lark" if _owns_tasks(command) else None
    if service and service not in services:
        return {"action": "block", "message": "任务创建时没有登记这项个人授权，请创建者先在对话中确认所需授权后再创建任务。"}
    return None


def required_services(spec):
    text = json.dumps({key: spec.get(key) for key in ("prompt", "skill", "skills", "script")}, ensure_ascii=False).lower()
    services = list(spec.get("personal_services") or [])
    if "meegle" in text or "飞书项目" in text:
        services.append("meegle")
    if re.search(r"lark-task|get-my-tasks|get-related-tasks|我的任务|本人任务|service\s*[=:]\s*['\"]?lark", text):
        services.append("lark")
    return sorted(set(services))


def verify_service(root, speaker, service):
    from .authorization import Authorization, AuthError, _data, _read, environment
    from .policy import meegle_home, lark_config_dir
    broker = Authorization(root)
    try:
        if service == "lark":
            folder = lark_config_dir(root, speaker)
            actual = _data(broker.run(["lark-cli", "api", "GET", "/open-apis/authen/v1/user_info", "--as", "user"], environment(root, service, folder), folder, timeout=10))
            if actual.get("open_id") != speaker:
                return False
        else:
            folder = meegle_home(root, speaker)
            actual = _data(broker.run(["meegle", "user", "me", "--format", "json"], environment(root, service, folder), folder, timeout=10))
            expected = _read(folder / "verified.json")
            if not actual.get("user_key") or actual.get("user_key") != expected.get("user_key"):
                return False
        return True
    except AuthError:
        return False


def creation_gate(args, speaker, root):
    action = args.get("action")
    if action not in {"create", "update", "resume", "run"}:
        return None
    if action == "create" and not speaker:
        return {"action": "block", "message": "无法确认任务创建者，请在 Lark 对话中创建。"}
    declared = args.get("personal_services")
    if action == "create" and declared is None:
        return {"action": "block", "message": "创建前必须用 personal_services 列出任务所需个人授权（lark/meegle）；无需个人授权填 []。缺授权先让本人完成授权，再创建任务。"}
    if declared is not None and (not isinstance(declared, list) or any(x not in ("lark", "meegle") for x in declared)):
        return {"action": "block", "message": "personal_services 只能是 lark/meegle 的列表。"}
    spec = dict(args)
    if action != "create":
        from cron.jobs import resolve_job_ref
        from .policy import canonical, load_links
        job = resolve_job_ref(args.get("job_id") or "")
        if not job:
            return None
        origin = job.get("origin") or {}
        if not isinstance(origin, dict) or origin.get("platform") != "feishu":
            return {"action": "block", "message": "任务没有已核实的 Lark 创建者，不能在对话中启用个人授权。"}
        speaker = str(job.get("personal_creator") or "")
        spec = {**job, **{k: v for k, v in args.items() if v is not None}}
    services = required_services(spec)
    if services and (spec.get("script") or spec.get("no_agent")):
        return {"action": "block", "message": "个人授权定时任务须通过受管工具执行；独立脚本不具备创建者授权绑定，请用 prompt 和 skills 创建。"}
    for service in services:
        valid = speaker and (has_meegle_grant(root, speaker) if service == "meegle" else has_lark_grant(root, speaker))
        if not valid or not verify_service(root, speaker, service):
            return {"action": "block", "message": f"本次定时任务操作未执行：创建者的 {service} 个人授权缺失或在线核验未通过。先检查 personal_auth 状态；缺授权时请创建者在 Lark 对话中使用 personal_auth(service={service}, action=start) 完成授权并 complete，再创建或启用。已有授权但核验失败时检查服务连接，不要把网络故障当成未授权；后台不会扫码或使用其他人的授权。"}
    return None
