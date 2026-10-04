"""Real job storage and approval guards; no business writes or model calls."""
import importlib.util
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cron_access_test', ROOT / 'extensions/lark-access/__init__.py', submodule_search_locations=[str(ROOT / 'extensions/lark-access')])
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
from cron_access_test import scheduled

@pytest.fixture
def live(tmp_path, monkeypatch):
    from cron.jobs import create_job, update_job
    from hermes_cli.config import _LOAD_CONFIG_CACHE
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_CRON_SESSION', '1')
    (tmp_path/'config.yaml').write_text('approvals:\n  cron_mode: deny\nsecurity:\n  tirith_enabled: false\n')
    (tmp_path/'lark-access').mkdir()
    config = tmp_path/'lark-access/config.json'
    config.write_text(json.dumps({'admins':['ou_admin']}))
    _LOAD_CONFIG_CACHE.clear()
    job = create_job('test', '1h', origin={'platform':'feishu','user_id':'ou_other'}, paused=True)
    update_job(job['id'], {'personal_creator':'ou_admin'})
    yield f"cron:{job['id']}:test", config
    _LOAD_CONFIG_CACHE.clear()


def test_creator_controls_permission_and_revocation(live):
    from tools.approval import check_all_command_guards
    from tools.approval_context import _get_cron_approval_mode
    task, config = live
    command = {'command':'rm -rf /tmp/permission-fixture-only'}
    run = lambda args: check_all_command_guards(args['command'], 'local')
    assert not run(command)['approved']
    assert scheduled.execute_as_creator(command, run, task_id=task)['approved']
    assert _get_cron_approval_mode() == 'deny'
    assert plugin._on_tool('write_file', {'path':'/opt/data/SOUL.md'}, task_id=task) is None
    assert plugin._on_tool('terminal', {'command':'hermes plugins install demo'}, task_id=task)['action'] == 'block'
    config.write_text(json.dumps({'admins':['ou_other']}))
    assert 'error' in json.loads(scheduled.execute_as_creator(command, run, task_id=task))
    assert plugin._on_tool('terminal', command, task_id=task)['action'] == 'block'


def test_missing_identity_and_concurrent_scope(live):
    from tools.approval_context import _get_cron_approval_mode
    task, _ = live
    with ThreadPoolExecutor(4) as pool:
        values = list(pool.map(lambda i: scheduled.execute_as_creator({}, lambda _: _get_cron_approval_mode(), task_id=task) if i%2 else _get_cron_approval_mode(), range(40)))
    assert values == ['deny', 'approve']*20
    assert 'error' in json.loads(scheduled.execute_as_creator({}, lambda _: 'unexpected', task_id='cron:missing:r'))


def test_hardline_still_blocks(live):
    from tools.approval import check_all_command_guards
    task, _ = live
    result = scheduled.execute_as_creator({}, lambda _: check_all_command_guards('rm -rf /', 'local'), task_id=task)
    assert not result['approved']
