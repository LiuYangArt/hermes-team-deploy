"""Team account behavior, with simulated upstream only; no business writes."""
import importlib
import json
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

EXT = Path(__file__).resolve().parents[1] / 'extensions' / 'personal-auth'
pkg = types.ModuleType('team_test_pkg')
pkg.__path__ = [str(EXT)]
sys.modules[pkg.__name__] = pkg
team = importlib.import_module('team_test_pkg.team_meegle')
policy = importlib.import_module('team_test_pkg.team_policy')


class TeamTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = {'default_project': 'project', 'default_type': 'demand', 'description_field': 'description'}
        team._write(self.root / 'team-meegle/config.json', self.cfg)
        team._write(self.root / 'lark-access/config.json', {'admins': ['ou_admin'], 'pm': 'ou_pm'})
        self.person = {'id': 'ou_member', 'name': 'Member', 'message_id': 'om_first', 'chat_id': 'oc_chat'}
        self.calls = []
        self.broker = team.TeamMeegle(self.root, self.cli)
        self.args = {'action': 'run', 'command': 'workitem.create', 'params': {'fields': [{'field_key': 'name', 'field_value': 'Test'}]}}

    def grant(self):
        folder = team.meegle_home(self.root, 'ou_pm')
        team._write(folder / 'verified.json', {'speaker': 'ou_pm', 'user_key': 'pm_user'})
        (folder / '.meegle').mkdir()
        (folder / '.meegle/credentials.enc').write_bytes(b'fake')

    def cli(self, argv, env, cwd, **kwargs):
        self.calls.append((argv, env, cwd))
        if argv[:3] == ['meegle', 'user', 'me']:
            return {'user_key': 'pm_user'}
        if argv[:3] == ['meegle', 'workitem', 'create']:
            return {'work_item_id': '123', 'url': 'https://example.test/item/123'}
        if argv[0] == 'lark-cli':
            return {'ok': True, 'data': {'message_id': 'om_notice'}}
        return {'id': '123'}

    def test_member_create_uses_pm_and_preserves_requester(self):
        self.grant()
        result = self.broker.handle(self.args, self.person)
        self.assertTrue(result['ok'])
        self.assertEqual(result['requester']['id'], 'ou_member')
        self.assertEqual(result['credential_owner'], 'ou_pm')
        argv, env, _ = self.calls[1]
        self.assertIn('ou_pm', env['HOME'])
        self.assertIn('--work-item-type=demand', argv)
        fields = json.loads(next(a.split('=', 1)[1] for a in argv if a.startswith('--fields=')))
        self.assertIn('ou_member', fields[-1]['field_value'])
        self.assertIn('readback', result)

    def test_explicit_pool_overrides_default(self):
        self.grant()
        self.args['params']['work-item-type'] = 'issue'
        self.broker.handle(self.args, self.person)
        self.assertIn('--work-item-type=issue', self.calls[1][0])

    def test_concurrent_duplicate_is_single_write(self):
        self.grant()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.broker.handle(self.args, self.person), range(4)))
        self.assertEqual(len({r['request_id'] for r in results}), 1)
        self.assertEqual(sum(c[0][:3] == ['meegle', 'workitem', 'create'] for c in self.calls), 1)

    def test_pending_restarts_and_pm_resume_preserves_original(self):
        pending = self.broker.handle(self.args, self.person)
        self.assertEqual(pending['state'], 'awaiting_authorization')
        self.assertFalse(self.calls)
        self.grant()
        result = team.TeamMeegle(self.root, self.cli).handle({'action': 'resume', 'request_id': pending['request_id']}, {**self.person, 'id': 'ou_pm'})
        self.assertEqual(result['requester']['id'], 'ou_member')
        self.assertTrue(result['ok'])

    def test_other_person_or_chat_cannot_resume(self):
        result = self.broker.handle(self.args, self.person)
        for person in ({**self.person, 'id': 'ou_other'}, {**self.person, 'chat_id': 'oc_other', 'id': 'ou_pm'}):
            with self.assertRaises(ValueError):
                self.broker.handle({'action': 'resume', 'request_id': result['request_id']}, person)

    def test_uncertain_write_is_never_replayed(self):
        self.grant()
        def uncertain(argv, *a, **kw):
            if argv[:3] == ['meegle', 'user', 'me']:
                return {'user_key': 'pm_user'}
            raise team.AuthError('timeout', 'timeout')
        broker = team.TeamMeegle(self.root, uncertain)
        result = broker.handle(self.args, self.person)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(self.broker.handle(self.args, self.person)['state'], 'uncertain')
        self.assertFalse(self.calls)

    def test_non_pm_admin_is_not_pm(self):
        with self.assertRaises(ValueError):
            self.broker.handle({'action': 'run', 'command': 'workitem.update', 'params': {}}, {**self.person, 'id': 'ou_admin'})
        self.assertFalse(self.calls)

    def test_pm_can_update_business_fields(self):
        self.grant()
        result = self.broker.handle({'action': 'run', 'command': 'workitem.update', 'params': {'project-key': 'p', 'work-item-id': '1', 'fields': []}}, {**self.person, 'id': 'ou_pm'})
        self.assertTrue(result['ok'])

    def test_comment_and_transition_are_not_owner_limited(self):
        self.grant()
        for command, params in [('comment.add', {'work-item-id': 'others', 'content': 'hello'}), ('workflow.transition-state', {'work-item-id': 'others', 'transition-id': 'next'})]:
            self.assertTrue(self.broker.handle({'action': 'run', 'command': command, 'params': params}, self.person)['ok'])

    def test_reject_overrides_arbitrary_updates_and_comment_edits(self):
        bad = [('workitem.update', {}), ('workitem.delete', {}), ('comment.add', {'work-item-id': '1', 'content': 'x', 'action': 'update'}), ('workflow.transition-state', {'work-item-id': '1', 'transition-id': 'x', 'fields': []}), ('workitem.create', {'fields': [], 'params': '{}'}), ('workitem.create', {'fields': '@secret'})]
        for command, params in bad:
            with self.subTest(command=command, params=params), self.assertRaises(ValueError):
                self.broker.handle({'action': 'run', 'command': command, 'params': params}, self.person)
        self.assertFalse(self.calls)

    def test_pm_configuration_is_fail_closed(self):
        team._write(self.root / 'lark-access/config.json', {'admins': ['ou_member']})
        with self.assertRaises(ValueError):
            self.broker.handle({'action': 'status'}, self.person)

    def test_reads_are_fresh_after_another_operation(self):
        self.grant()
        args = {'action': 'run', 'command': 'workitem.get', 'params': {'work-item-id': '123'}}
        first = self.broker.handle(args, self.person)
        second = self.broker.handle(args, self.person)
        self.assertNotEqual(first['request_id'], second['request_id'])
        self.assertEqual(sum(c[0][:3] == ['meegle', 'workitem', 'get'] for c in self.calls), 2)

    def test_notification_is_deduplicated_and_targets_pm(self):
        class Auth:
            def handle(self, speaker, service, action, host):
                self.speaker = speaker
                return {'ok': True, 'status': 'pending', 'verification_url': 'https://example.test/device', 'expires_in': 300}
        auth = Auth()
        broker = team.TeamMeegle(self.root, self.cli, auth)
        one = broker.handle({'action': 'auth_start'}, self.person)
        two = broker.handle({'action': 'auth_start'}, {**self.person, 'message_id': 'om_second'})
        self.assertEqual(auth.speaker, 'ou_pm')
        self.assertEqual(one['notice_message_id'], two['notice_message_id'])
        self.assertEqual(len(self.calls), 1)
        self.assertIn('<at user_id="ou_pm">PM</at>', self.calls[0][0][self.calls[0][0].index('--text')+1])


if __name__ == '__main__':
    unittest.main()
