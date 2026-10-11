"""Team delegation keeps the request owner separate from the credential owner."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .authorization import Authorization, AuthError, _read, _write, environment, run_cli, _data
from .policy import canonical, load_links, has_meegle_grant, has_lark_grant, meegle_home
from .team_policy import validate_operation, ORDINARY_COMMANDS, WRITES, DOMAINS

SCHEMA = {
    'name': 'team_meegle',
    'description': '团队 Meegle 代办。请求者由当前 Lark 消息绑定，执行账号为配置的 PM。普通成员只开单、添加评论、修改状态及必要读取；PM 不限业务操作。缺授权自动在 Lark 原话题通知 PM；扫码后 auth_complete 恢复原请求。默认目标由受保护配置提供。不得改用 terminal 绕过。',
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['status', 'inspect', 'run', 'auth_start', 'auth_complete', 'resume']},
        'command': {'type': 'string', 'description': '点号业务命令，例如 workitem.create、comment.add、workflow.transition-state'},
        'params': {'type': 'object', 'description': 'CLI 参数，用 project-key、work-item-id 等短横线名称；不能含 --params、--set 或账号配置。'},
        'request_id': {'type': 'string', 'description': '恢复或继续原请求时使用工具已返回的请求编号。PM 授权后继续 inspect/run 必须携带原编号，保留原请求成员。'},
    }, 'required': ['action'], 'additionalProperties': False},
}

NOTE = (
    '团队 Meegle 已启用：所有交互请求使用 team_meegle，不再要求普通成员登录 Meegle。'
    '先 status 取得当前请求人、PM 和默认目标；普通成员可为所有人的单开单、添加评论、修改状态，不能改其他字段或删除。'
    '当前消息真实发送者是 PM 时不限制 Meegle 业务操作；管理员和 PM 独立。'
    '“我”是提出该请求的 Lark 成员，不是登录账号；负责人没有明确指定时不要自动填 PM。'
    '未指定池子采用配置的默认目标，明确指定其他池子先查 meta-types。'
    '元数据、必填字段和可用状态都通过 team_meegle 查询。不得从个人终端、脚本或其他工具绕过。'
    '明确开单、评论、改状态后直接执行并回读，不再次索要相同写入确认。'
    '缺授权时工具自动在原话题 @PM 并发送二维码，不要求成员扫码或转发链接。PM 扫码后需在原话题 @机器人回复“已授权”，再 auth_complete；已保存操作自动恢复，不重复提交。'
    'PM 扫码不改变原请求人。超时或结果不确定时只回读核实，不能重复提交。'
    'auth_complete 返回恢复结果后，后续 inspect/run 必须携带对应 request_id；尤其 PM 确认授权后，不能把原成员的业务请求当成 PM 的新请求。'
)


def config(root):
    path = root / 'team-meegle' / 'config.json'
    if not path.exists():
        return None
    value = _read(path)
    pm = _read(root / 'lark-access' / 'config.json').get('pm')
    if not isinstance(pm, str) or not re.fullmatch(r'ou_[a-zA-Z0-9]+', pm):
        raise ValueError('团队 PM 未正确配置，暂停 Meegle 代办。')
    if not value.get('default_project') or not value.get('default_type'):
        raise ValueError('团队 Meegle 默认目标未配置。')
    return {**value, 'pm': pm}


def identity(task_id=''):
    from gateway.session_context import get_session_env
    if str(task_id).startswith('cron:') or get_session_env('HERMES_CRON_SESSION', '') == '1':
        raise ValueError('定时任务沿用创建者授权，不使用团队交互代办。')
    if get_session_env('HERMES_SESSION_PLATFORM', '') != 'feishu':
        raise ValueError('团队代办需要来自 Lark 的真实请求。')
    from hermes_constants import get_hermes_home
    root = Path(get_hermes_home())
    speaker = canonical((get_session_env('HERMES_SESSION_USER_ID', ''), get_session_env('HERMES_SESSION_USER_ID_ALT', '')), load_links(root / 'personal-auth' / 'speakers.json'))
    person = {'id': speaker, 'name': get_session_env('HERMES_SESSION_USER_NAME', ''),
              'message_id': get_session_env('HERMES_SESSION_MESSAGE_ID', ''),
              'chat_id': get_session_env('HERMES_SESSION_CHAT_ID', ''),
              'thread_id': get_session_env('HERMES_SESSION_THREAD_ID', '')}
    if not speaker.startswith('ou_') or not person['message_id'] or not person['chat_id']:
        raise ValueError('缺少可信消息来源，不能代办。')
    return root, person


def handle(args, task_id='', **kwargs):
    try:
        root, requester = identity(task_id)
        return json.dumps(TeamMeegle(root).handle(args, requester), ensure_ascii=False)
    except (ValueError, AuthError) as exc:
        return json.dumps({'ok': False, 'error': getattr(exc, 'kind', 'invalid_request'), 'message': str(getattr(exc, 'message', exc))}, ensure_ascii=False)


class TeamMeegle:
    def __init__(self, root, runner=run_cli, authorizer=None):
        self.root, self.run = Path(root), runner
        self.authorizer = authorizer or Authorization(self.root, runner=runner)
        self.store = self.root / 'team-meegle'

    def handle(self, args, requester):
        cfg = config(self.root)
        if cfg is None:
            raise ValueError('未启用团队 Meegle。')
        if not requester.get('id', '').startswith('ou_') or not requester.get('message_id') or not requester.get('chat_id'):
            raise ValueError('缺少可信请求者。')
        if not isinstance(args, dict) or set(args) - {'action', 'command', 'params', 'request_id'}:
            raise ValueError('无效参数。')
        self.store.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.store.chmod(0o700)
        with (self.store / 'requests.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._handle(args, requester, cfg)

    def _handle(self, args, person, cfg):
        action = args.get('action')
        pm = cfg['pm']
        if action == 'status':
            return {'ok': True, 'requester': person, 'credential_owner': pm, 'is_pm': person['id'] == pm,
                    'authorized': has_meegle_grant(self.root, pm), 'default_project': cfg['default_project'],
                    'default_type': cfg['default_type'], 'default_pool_name': cfg.get('default_pool_name'),
                    'message': '这是已保存的绑定状态；实际工具调用仍核验上游登录。'}
        if action in {'auth_start', 'auth_complete'}:
            return self._authorize(action, person, cfg)
        if action == 'resume':
            request_id = args.get('request_id', '')
            if not re.fullmatch(r'[a-f0-9]{32}', request_id):
                raise ValueError('无效请求编号。')
            record = _read(self.store / 'requests' / f'{request_id}.json')
            if not record or record['requester']['chat_id'] != person['chat_id']:
                raise ValueError('请求不存在或不属于当前群。')
            if person['id'] not in {pm, record['requester']['id']}:
                raise ValueError('只有原请求人或 PM 可恢复这个请求。')
            if record.get('credential_owner') != pm:
                raise ValueError('PM 已更换，需要原请求人重新提出请求。')
            if record['state'] != 'awaiting_authorization':
                if record['state'] == 'succeeded' and person['message_id'] != record['requester']['message_id']:
                    self._notify_completed(record, cfg)
                return self._receipt(record)
            outcome = self._execute(record, cfg)
            if outcome.get('ok') and person['message_id'] != record['requester']['message_id']:
                self._notify_completed(record, cfg)
            return outcome
        if args.get('request_id'):
            request_id = args['request_id']
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id):
                raise ValueError('无效请求编号。')
            previous = _read(self.store / 'requests' / f'{request_id}.json')
            owner = previous.get('requester', {})
            if (not owner or previous.get('credential_owner') != pm
                    or owner.get('chat_id') != person['chat_id']
                    or (owner.get('thread_id') or owner.get('message_id')) != (person.get('thread_id') or person['message_id'])
                    or person['id'] not in {pm, owner['id']}):
                raise ValueError('只能由原请求人或 PM 在原话题继续请求。')
            person = owner
        command, params = args.get('command'), args.get('params', {})
        if action == 'inspect':
            if not isinstance(command, str) or not re.fullmatch(r'[a-z]+\.\+?[a-z][a-z-]*', command):
                raise ValueError('需要明确业务命令。')
            if command.split('.')[0] not in DOMAINS or (person['id'] != pm and command not in ORDINARY_COMMANDS):
                raise ValueError('此操作不在当前成员的权限内。')
            if not has_meegle_grant(self.root, pm):
                digest = json.dumps([pm, person['id'], person['message_id'], 'inspect', command])
                record = {'id': hashlib.sha256(digest.encode()).hexdigest()[:32],
                          'requester': person, 'credential_owner': pm, 'action': 'inspect',
                          'command': command, 'params': {}, 'state': 'awaiting_authorization',
                          'created_at': time.time()}
                self._save(record)
                return self._execute(record, cfg)
            return self._call(['inspect', command], cfg)
        if action != 'run':
            raise ValueError('无效操作。')
        validate_operation(command, params, is_pm=person['id'] == pm, config=cfg, requester=person)
        # A request is keyed to the actual message and operation, never a model-supplied user id.
        digest = json.dumps([pm, person['id'], person['message_id'], command, params], sort_keys=True, ensure_ascii=False)
        request_id = hashlib.sha256(digest.encode()).hexdigest()[:32]
        if command in ORDINARY_COMMANDS - WRITES:
            request_id = uuid.uuid4().hex
        path = self.store / 'requests' / f'{request_id}.json'
        previous = _read(path)
        if previous:
            if previous['state'] == 'awaiting_authorization':
                return self._execute(previous, cfg)
            return self._receipt(previous)
        record = {'id': request_id, 'requester': person, 'credential_owner': pm, 'command': command,
                  'params': params, 'state': 'awaiting_authorization', 'created_at': time.time()}
        _write(path, record)
        return self._execute(record, cfg)

    def _save(self, record):
        _write(self.store / 'requests' / f"{record['id']}.json", record)

    def _execute(self, record, cfg):
        pm = cfg['pm']
        if not has_meegle_grant(self.root, pm):
            receipt = self._receipt(record)
            receipt['authorization'] = self._authorize('auth_start', record['requester'], cfg)
            return receipt
        if record.get('action') == 'inspect':
            result = self._call(['inspect', record['command']], cfg)
            record.update(state='succeeded', result=result, finished_at=time.time())
            self._save(record)
            return self._receipt(record)
        command, params = validate_operation(record['command'], record['params'], is_pm=record['requester']['id'] == pm, config=cfg, requester=record['requester'])
        # Before a write, distinguish a revoked login from a failed/uncertain business request.
        try:
            who = _data(self._call(['user', 'me'], cfg))
        except AuthError as exc:
            return {'ok': False, 'request_id': record['id'], 'state': record['state'], 'error': exc.kind,
                    'message': 'PM 账号在线核验未成功；若上游明确表示未登录或已过期，用 auth_start 让 PM 重新授权。网络或业务权限错误不能当成未登录。'}
        verified = _read(meegle_home(self.root, pm) / 'verified.json')
        if not who.get('user_key') or who['user_key'] != verified.get('user_key'):
            raise ValueError('当前 Meegle 登录账号与已核验的 PM 不一致。')
        record['state'] = 'executing'
        self._save(record)
        argv = command.split('.')
        for key, value in params.items():
            encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                argv.extend(f'--{key}={v}' for v in value)
            else:
                argv.append(f'--{key}={encoded}')
        try:
            result = self._call(argv, cfg)
        except AuthError as exc:
            record.update(state='uncertain', error=exc.kind)
            self._save(record)
            return self._receipt(record)
        record.update(state='succeeded', result=result, finished_at=time.time())
        self._save(record)
        if command in WRITES:
            item_id = params.get('work-item-id') or _item_id(result)
            if item_id:
                try:
                    readback = self._call(['workitem', 'get', '--project-key=' + params['project-key'], '--work-item-id=' + str(item_id)], cfg)
                    if command == 'comment.add':
                        readback = {'workitem': readback, 'comments': self._call(['comment', 'list', '--project-key=' + params['project-key'], '--work-item-id=' + str(item_id)], cfg)}
                    record['readback'] = readback
                    self._save(record)
                except AuthError:
                    record['readback_error'] = '写入已返回成功，回读失败；不能重新提交。'
                    self._save(record)
            else:
                record['readback_error'] = '写入已返回结果，但未识别单号；必须核实结果，不能重新提交。'
                self._save(record)
        return self._receipt(record)

    def _notify_completed(self, record, cfg):
        if record.get('completion_message_id'):
            return
        env = environment(self.root, 'lark', self.root / '.lark-cli')
        env['LARKSUITE_CLI_CONFIG_DIR'] = str(self.root / '.lark-cli')
        env['LARKSUITE_CLI_DATA_DIR'] = str(self.root / '.local' / 'share')
        person = record['requester']
        item = record['params'].get('work-item-id') or _item_id(record.get('result'))
        text = f"原请求已恢复并执行成功。请求人：{person.get('name') or person['id']}；操作：{record['command']}；单号：{item or '见工具返回'}。"
        try:
            sent = self.run(['lark-cli', 'im', '+messages-reply', '--message-id', person['message_id'], '--reply-in-thread', '--text', text, '--as', 'bot', '--idempotency-key', 'pm-done-' + record['id'], '--format', 'json'], env, self.root)
            record['completion_message_id'] = _item_id(sent, ('message_id',))
            self._save(record)
        except AuthError:
            record['delivery_error'] = '业务操作已成功，原话题通知未确认送达；不能重新执行业务操作。'
            self._save(record)

    def _call(self, argv, cfg):
        folder = meegle_home(self.root, cfg['pm'])
        if argv[0] == 'inspect':
            return inspect_cli(argv[1], environment(self.root, 'meegle', folder), folder)
        return self.run(['meegle', *argv, '--format', 'json'], environment(self.root, 'meegle', folder), folder)

    def _receipt(self, record):
        state = record['state']
        message = {'awaiting_authorization': '原请求已保存，等待 PM 在 Lark 完成授权；通知送达情况见 authorization。身份仍是原请求人。',
                   'executing': '之前执行尚无确定结果，禁止重复提交，请先回读核实。',
                   'uncertain': '操作结果不确定，禁止重复提交，请先回读核实。',
                   'succeeded': '操作已返回成功，请依据 result/readback 给出实际单号和链接。'}[state]
        return {'ok': state == 'succeeded', 'request_id': record['id'], 'state': state,
                'requester': record['requester'], 'credential_owner': record['credential_owner'],
                'result': record.get('result'), 'readback': record.get('readback'),
                'readback_error': record.get('readback_error'), 'message': message}

    def _authorize(self, action, person, cfg):
        pm = cfg['pm']
        service = 'meegle' if has_lark_grant(self.root, pm) else 'lark'
        if action == 'auth_complete':
            result = self.authorizer.handle(pm, service, 'complete', cfg.get('host', 'meegle.com'))
            if result.get('status') == 'authorized' and service == 'lark':
                service = 'meegle'
                result = self.authorizer.handle(pm, service, 'start', cfg.get('host', 'meegle.com'))
        else:
            result = self.authorizer.handle(pm, service, 'start', cfg.get('host', 'meegle.com'))
        result['credential_owner'] = pm
        result['service'] = service
        if result.get('status') == 'pending':
            result['message'] = '等待 PM 在本话题扫码；完成后请 PM 在本话题 @机器人回复“已授权”。普通成员无需登录或转发。'
            scope = [person['chat_id'], person.get('thread_id') or person['message_id']]
            fingerprint = hashlib.sha256(json.dumps([scope, result['verification_url']]).encode()).hexdigest()
            notice = self.store / 'notices' / (fingerprint + '.json')
            previous = _read(notice)
            bot_env = environment(self.root, 'lark', self.root / '.lark-cli')
            bot_env['LARKSUITE_CLI_CONFIG_DIR'] = str(self.root / '.lark-cli')
            bot_env['LARKSUITE_CLI_DATA_DIR'] = str(self.root / '.local' / 'share')
            try:
                if not previous.get('message_id'):
                    text = f'<at user_id="{pm}">{cfg.get("pm_name", "PM")}</at> 团队 Meegle 需要你本人完成 {service} 授权：\n{result["verification_url"]}\n有效期 {result["expires_in"]} 秒。完成后请在本话题 @机器人回复“已授权”，我会继续原请求。其他成员无需登录。'
                    sent = self.run(['lark-cli', 'im', '+messages-reply', '--message-id', person['message_id'], '--reply-in-thread', '--text', text, '--as', 'bot', '--idempotency-key', 'pm-auth-' + fingerprint[:32], '--format', 'json'], bot_env, self.root)
                    message_id = _item_id(sent, ('message_id',))
                    if not message_id:
                        raise AuthError('notice_unconfirmed', '授权通知未返回消息编号。')
                    previous.update(message_id=message_id)
                    _write(notice, previous)
                qr = result.get('qr_path')
                if qr and not previous.get('qr_message_id'):
                    path = Path(qr)
                    sent = self.run(['lark-cli', 'im', '+messages-reply', '--message-id', person['message_id'], '--reply-in-thread', '--image', path.name, '--as', 'bot', '--idempotency-key', 'pm-qr-' + fingerprint[:32], '--format', 'json'], bot_env, path.parent)
                    message_id = _item_id(sent, ('message_id',))
                    if not message_id:
                        raise AuthError('notice_unconfirmed', '二维码未返回消息编号。')
                    previous['qr_message_id'] = message_id
                    _write(notice, previous)
            except AuthError as exc:
                result['delivery_error'] = exc.kind
                result['message'] = 'Lark 授权通知未完整送达，请重试 auth_start；原请求仍保留，不要求成员转发链接。'
            result['notice_message_id'] = previous.get('message_id')
            result['qr_message_id'] = previous.get('qr_message_id')
            # The transport owns delivery; the model must not copy an authorization link elsewhere.
            result.pop('verification_url', None)
            result.pop('qr_path', None)
        elif result.get('status') == 'authorized':
            pending = [r for p in (self.store / 'requests').glob('*.json')
                       if (r := _read(p)) and r.get('state') == 'awaiting_authorization'
                       and r['credential_owner'] == pm
                       and r['requester']['chat_id'] == person['chat_id']
                       and (r['requester'].get('thread_id') or r['requester']['message_id']) == (person.get('thread_id') or person['message_id'])
                       and person['id'] in {pm, r['requester']['id']}]
            result['resumed_requests'] = []
            for record in sorted(pending, key=lambda r: r['created_at']):
                outcome = self._execute(record, cfg)
                result['resumed_requests'].append(outcome)
            result['message'] = 'PM 授权已保存；已恢复本话题原请求。按结果答复，后续 inspect/run 携带对应 request_id 保留原请求人，不重复提交。'

        return result


def inspect_cli(command, env, folder):
    import subprocess
    try:
        result = subprocess.run(['meegle', 'inspect', command], env=env, cwd=folder, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise AuthError('inspect_failed', '无法读取命令定义。') from None
    if result.returncode:
        raise AuthError('inspect_failed', '无法读取命令定义。')
    return {'ok': True, 'definition': result.stdout[:16000]}


def _item_id(value, keys=('work_item_id', 'workItemId', 'id')):
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), (str, int)):
                return value[key]
        for key in ('data', 'work_item', 'result'):
            found = _item_id(value.get(key), keys)
            if found:
                return found
    return None

