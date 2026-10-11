"""Capability policy for a configured team's Meegle account, independent of the payer."""
from __future__ import annotations
import copy
import re

WRITES = {'workitem.create', 'comment.add', 'workflow.transition', 'workflow.transition-state'}
SPECS = {
    'workitem.create': ({'project-key', 'work-item-type', 'fields'}, set()),
    'comment.add': ({'project-key', 'work-item-id', 'content'}, {'action'}),
    'workflow.transition-state': ({'project-key', 'work-item-id', 'transition-id'}, set()),
    'workflow.transition': ({'project-key', 'work-item-id', 'node-id', 'action'}, {'rollback-reason'}),
    'workitem.meta-types': ({'project-key'}, set()),
    'workitem.meta-fields': ({'project-key', 'work-item-type'}, set()),
    'workitem.meta-create-fields': ({'project-key', 'work-item-type'}, set()),
    'workitem.meta-roles': ({'project-key', 'work-item-type'}, set()),
    'workitem.get': ({'project-key', 'work-item-id'}, {'fields', 'page-size', 'page-token'}),
    'comment.list': ({'project-key', 'work-item-id'}, {'page-num', 'start-time', 'end-time'}),
    'workflow.get-node': ({'project-key', 'work-item-id'}, {'node-id-list', 'field-key-list', 'need-sub-task', 'page-num'}),
    'workflow.list-state-transitions': ({'project-key', 'work-item-id'}, set()),
    'workflow.list-state-required': ({'state-key'}, {'project-key', 'work-item-id', 'mode'}),
    'user.search': ({'user-keys'}, {'project-key'}),
}
ORDINARY_COMMANDS = set(SPECS)
DOMAINS = {'workitem', 'workflow', 'comment', 'workhour', 'relation', 'mywork', 'view', 'chart', 'team', 'user', 'project', 'subtask', 'attachment', 'deliverable', 'resource', 'wbs', 'url'}
FORBIDDEN = {'params', 'set', 'profile', 'output', 'output-dir', 'file', 'file-path', 'token', 'access-token', 'host', 'config', 'refresh', 'format', 'help'}


def validate_operation(command, params, *, is_pm, config, requester):
    if not isinstance(command, str) or not re.fullmatch(r'[a-z]+\.\+?[a-z][a-z-]*', command):
        raise ValueError('必须使用明确的 Meegle 业务命令。')
    if command.split('.')[0] not in DOMAINS:
        raise ValueError('账号、凭据和 CLI 本机配置不属于 Meegle 业务操作。')
    if not isinstance(params, dict) or not requester.get('id', '').startswith('ou_'):
        raise ValueError('无法核实请求成员或参数格式。')
    result = copy.deepcopy(params)
    for key, value in result.items():
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', key) or key in FORBIDDEN:
            raise ValueError('不能覆盖账号、执行环境或 CLI 全局参数。')
        if isinstance(value, str) and (value.startswith('@') or '\x00' in value):
            raise ValueError('参数不能从本机文件读取。')
    if not is_pm and command not in SPECS:
        raise ValueError('普通成员仅能开单、添加评论、修改状态及读取操作所需信息。')
    if command in SPECS:
        required, optional = SPECS[command]
        if 'project-key' in required:
            result.setdefault('project-key', config.get('default_project'))
        if 'work-item-type' in required:
            if result.get('project-key') == config.get('default_project'):
                result.setdefault('work-item-type', config.get('default_type'))
        if not is_pm and set(result) - required - optional:
            raise ValueError('此操作包含普通成员无权修改的参数。')
        if any(key not in result or result[key] in (None, '', [], {}) for key in required):
            raise ValueError('缺少必要信息，请先查询元数据或询问请求成员。')
        if any(not isinstance(result[key], str) for key in required - {'fields', 'user-keys'}):
            raise ValueError('编号与文本参数必须为字符串。')
    if command == 'user.search':
        keys = result.get('user-keys')
        if not isinstance(keys, list) or not keys or len(keys) > 20 or any(not isinstance(k, str) or not k or k.startswith('@') or (not is_pm and 'current_login_user' in k) for k in keys):
            raise ValueError('人员查询必须指定实际成员，不使用登录账号代替“我”。')
    if command == 'workitem.create':
        fields = result.get('fields')
        if not isinstance(fields, list) or not fields:
            raise ValueError('建单字段必须是非空列表。')
        keys = []
        for field in fields:
            if not isinstance(field, dict) or set(field) != {'field_key', 'field_value'} or not isinstance(field['field_key'], str):
                raise ValueError('每个建单字段必须使用 field_key 和 field_value。')
            keys.append(field['field_key'])
        if len(set(keys)) != len(keys):
            raise ValueError('建单字段不能重复。')
        description = config.get('description_fields', {}).get(result['work-item-type'])
        if result['work-item-type'] == config.get('default_type'):
            description = description or config.get('description_field')
        if description:
            attribution = f"请求人：{requester.get('name') or requester['id']}（Lark：{requester['id']}）；由 PM 账号代办。"
            field = next((f for f in fields if f['field_key'] == description), None)
            if field:
                if not isinstance(field['field_value'], str):
                    raise ValueError('描述必须是文本，以保留真实请求人。')
                field['field_value'] = field['field_value'].rstrip() + '\n\n' + attribution
            else:
                fields.append({'field_key': description, 'field_value': attribution})
    if not is_pm and command == 'comment.add':
        if result.get('action', 'create') != 'create':
            raise ValueError('普通成员只能添加评论，不能修改已有评论。')
        result['action'] = 'create'
        result['content'] += f"\n\n请求人：{requester.get('name') or requester['id']}（Lark：{requester['id']}）；由 PM 账号代办。"
    if command == 'workflow.transition':
        if result.get('action') not in {'confirm', 'rollback'}:
            raise ValueError('仅支持合法的状态前进或回退。')
        if result['action'] == 'rollback' and not result.get('rollback-reason'):
            raise ValueError('回退状态需要填写原因。')
    return command, result
