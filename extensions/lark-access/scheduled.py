"""Apply stored creator authority within a single scheduled tool invocation."""
import json


def creator_identity(task_id):
    from cron.jobs import get_job
    parts = str(task_id).split(":")
    if len(parts) != 3 or parts[0] != "cron" or not all(parts[1:]):
        return "feishu", "", ""
    job = get_job(parts[1]) or {}
    origin = job.get("origin") or {}
    if not isinstance(origin, dict) or origin.get("platform") != "feishu":
        return "feishu", "", ""
    return "feishu", str(job.get("personal_creator") or ""), ""


def creator_is_admin(task_id):
    from . import _config_path
    from .policy import is_admin, load_config
    _, creator, _ = creator_identity(task_id)
    return bool(creator) and is_admin(load_config(_config_path()), creator)


def execute_as_creator(args, next_call, task_id="", **kwargs):
    if not str(task_id).startswith("cron:"):
        return next_call(args)
    # Recheck current membership: removing an administrator also revokes their jobs.
    try:
        if not creator_is_admin(task_id):
            return json.dumps({"error": "定时任务缺少已核实的管理员创建者，或创建者已被移出管理员名单。"}, ensure_ascii=False)
        from tools.approval_context import cron_approval_scope
    except Exception:
        return json.dumps({"error": "无法核验定时任务创建者的命令权限，本次未执行。"}, ensure_ascii=False)
    with cron_approval_scope("approve"):
        return next_call(args)
