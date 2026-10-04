"""Scheduled calls use saved creators, not the person triggering a manual run."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

EXT = Path(__file__).resolve().parents[1] / 'extensions' / 'personal-auth'
spec = importlib.util.spec_from_file_location('scheduled_auth_test', EXT / '__init__.py', submodule_search_locations=[str(EXT)])
plugin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin
spec.loader.exec_module(plugin)
from scheduled_auth_test import scheduled, policy


class ScheduledAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = {'a': {'id': 'a', 'origin': {'platform': 'feishu', 'user_id': 'ou_a'}, 'skills': ['meegle']},
                     'b': {'id': 'b', 'origin': {'platform': 'feishu', 'user_id': 'ou_b'}, 'skills': ['meegle']}}
        for job in self.jobs.values():
            job['personal_services'] = ['meegle']
        jobs = types.ModuleType('cron.jobs')
        jobs.get_job = jobs.resolve_job_ref = lambda ident: self.jobs.get(ident)
        session = types.ModuleType('gateway.session_context')
        session.get_session_env = lambda key, default='': {'HERMES_SESSION_PLATFORM': 'feishu', 'HERMES_SESSION_USER_ID': 'ou_b'}.get(key, default)
        self.modules = patch.dict(sys.modules, {'cron.jobs': jobs, 'gateway.session_context': session})
        self.modules.start()
        self.home = patch.object(plugin, '_home', return_value=self.root)
        self.home.start()
        folder = policy.meegle_home(self.root, 'ou_a')
        (folder / '.meegle').mkdir(parents=True)
        (folder / '.meegle' / 'credentials.enc').write_bytes(b'fake')
        (folder / 'verified.json').write_text(json.dumps({'speaker': 'ou_a'}))

    def tearDown(self):
        self.home.stop()
        self.modules.stop()
        self.tmp.cleanup()

    def test_cron_uses_creator_even_when_caller_is_another_person(self):
        decision = plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id='cron:a:run1')
        self.assertIn('/ou_a/', decision['args']['command'])
        self.assertNotIn('/ou_b/', decision['args']['command'])
        denied = plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id='cron:b:run2')
        self.assertEqual(denied['action'], 'block')
        status = json.loads(plugin._authorize({'service': 'meegle', 'action': 'status'}, task_id='cron:a:run1'))
        self.assertEqual(status['status'], 'authorized')

    def test_unknown_or_missing_creator_fails_closed(self):
        for task in ['cron:missing:run1', 'cron:a', 'cron::run1']:
            denied = plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id=task)
            self.assertEqual(denied['action'], 'block')
        self.jobs['a']['origin'] = None
        self.assertEqual(plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id='cron:a:r')['action'], 'block')

    def test_cron_cannot_login_logout_or_accept_model_identity(self):
        for action in ['start', 'complete', 'logout']:
            result = json.loads(plugin._authorize({'service': 'meegle', 'action': action, 'user_id': 'ou_b'}, task_id='cron:a:r'))
            self.assertEqual(result['error'], 'interactive_authorization_required')
        self.assertTrue(policy.has_meegle_grant(self.root, 'ou_a'))

    def test_creation_blocks_missing_or_expired_grant(self):
        args = {'action': 'create', 'skills': ['meegle'], 'schedule': '2h', 'personal_services': ['meegle']}
        self.assertEqual(scheduled.creation_gate(args, 'ou_b', self.root)['action'], 'block')
        with patch.object(scheduled, 'verify_service', return_value=False):
            self.assertEqual(scheduled.creation_gate(args, 'ou_a', self.root)['action'], 'block')
        with patch.object(scheduled, 'verify_service', return_value=True):
            self.assertIsNone(scheduled.creation_gate(args, 'ou_a', self.root))
        self.assertIsNone(scheduled.creation_gate({'action': 'create', 'prompt': '喝水提醒', 'personal_services': []}, 'ou_b', self.root))
        self.assertEqual(scheduled.creation_gate({'action': 'create', 'prompt': '喝水提醒'}, 'ou_b', self.root)['action'], 'block')

    def test_update_resume_run_check_creator_not_operator(self):
        with patch.object(scheduled, 'verify_service', return_value=True) as verify:
            for action in ['update', 'resume', 'run']:
                self.assertIsNone(scheduled.creation_gate({'action': action, 'job_id': 'a'}, 'ou_b', self.root))
                self.assertEqual(verify.call_args.args[1], 'ou_a')
                self.assertEqual(scheduled.creation_gate({'action': action, 'job_id': 'b'}, 'ou_a', self.root)['action'], 'block')

    def test_revoked_grant_stops_next_business_call(self):
        (policy.meegle_home(self.root, 'ou_a') / '.meegle' / 'credentials.enc').unlink()
        self.assertEqual(plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id='cron:a:r')['action'], 'block')

    def test_parallel_task_identities_do_not_share_mutable_context(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(scheduled.creator_identity, ['cron:a:r', 'cron:b:r'] * 20))
        self.assertEqual([x[1] for x in values], ['ou_a', 'ou_b'] * 20)


if __name__ == '__main__':
    unittest.main()

class ManagedCreationTest(ScheduledAuthTest):
    def test_create_is_paused_until_requirements_are_persisted(self):
        calls = []
        def create(args, **kwargs):
            self.assertTrue(args['paused'])
            calls.append('created-paused')
            self.jobs['new'] = {'id': 'new', 'enabled': False, 'origin': {'platform': 'feishu', 'user_id': 'ou_b'}}
            return json.dumps({'success': True, 'job_id': 'new'})
        def update(ident, values):
            self.assertFalse(self.jobs[ident]['enabled'])
            calls.append('requirements-saved')
            self.jobs[ident].update(values)
            return self.jobs[ident]
        def resume(ident):
            self.assertIn('personal_services', self.jobs[ident])
            calls.append('resumed')
            self.jobs[ident]['enabled'] = True
            return self.jobs[ident]
        from unittest.mock import Mock
        tools = types.ModuleType('tools.cronjob_tools')
        tools._cronjob_handler = create
        tools._notify_provider_jobs_changed_safe = Mock()
        shaping = types.ModuleType('tools.cronjob_job_args')
        shaping._format_job = lambda job: dict(job)
        jobs = sys.modules['cron.jobs']
        jobs.update_job, jobs.resume_job = update, resume
        with patch.dict(sys.modules, {'tools.cronjob_tools': tools, 'tools.cronjob_job_args': shaping}):
            result = json.loads(scheduled.manage_cron({'action': 'create', 'schedule': '2h', 'prompt': '提醒', 'personal_services': []}))
        self.assertTrue(result['success'])
        self.assertEqual(calls, ['created-paused', 'requirements-saved', 'resumed'])
        self.assertEqual(self.jobs['new']['origin']['user_id'], 'ou_b')

    def test_undeclared_services_cannot_be_used_in_background(self):
        self.jobs['a']['personal_services'] = []
        result = plugin._on_tool('terminal', {'command': 'meegle user me'}, task_id='cron:a:r')
        self.assertEqual(result['action'], 'block')
        result = json.loads(plugin._authorize({'service': 'meegle', 'action': 'status'}, task_id='cron:a:r'))
        self.assertEqual(result['error'], 'undeclared_personal_service')
