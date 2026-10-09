"""Exercise the deployed Core approval matcher without executing Meegle writes."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('HERMES_CORE_PATH', ROOT.parent / 'hermes-team'))
sys.path.insert(0, str(CORE))
from tools import approval
from tools.approval_floors import _command_matches_permanent_allowlist

spec = importlib.util.spec_from_file_location('create_policy', ROOT / 'extensions/personal-auth/policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class CreateApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        home = policy.meegle_home(self.root, 'ou_test')
        (home / '.meegle').mkdir(parents=True)
        (home / '.meegle/credentials.enc').write_bytes(b'test')
        (home / 'verified.json').write_text(json.dumps({'speaker': 'ou_test'}))
        self.patterns = {
            f'env HOME={self.root}/personal-auth/*/meegle-home USER=hermes {exe} workitem create *'
            for exe in ('meegle', '/usr/local/bin/meegle')
        }
        from unittest.mock import patch
        self.allowlist = patch.object(approval, '_permanent_set', return_value=self.patterns)
        self.allowlist.start()
        self.addCleanup(self.allowlist.stop)
        self.addCleanup(self.tmp.cleanup)

    def command(self, text, speaker='ou_test'):
        return policy.tool_decision(user_ids=(speaker,), tool_name='terminal',
                                    args={'command': text}, root=self.root, links={})

    def test_create_with_chinese_text_matches_only_after_identity_binding(self):
        fields = json.dumps([{'field_key': 'name', 'field_value': '降低 GPU 耗时。'}], ensure_ascii=False)
        for exe in ('meegle', '/usr/local/bin/meegle'):
            raw = f"{exe} workitem create --fields '{fields}' --project-key test"
            self.assertFalse(_command_matches_permanent_allowlist(raw))
            bound = self.command(raw)['args']['command']
            self.assertTrue(_command_matches_permanent_allowlist(bound))

    def test_other_operations_and_shell_chains_do_not_match(self):
        for operation in ('update', 'delete', 'search'):
            bound = self.command(f'meegle workitem {operation} --project-key test')['args']['command']
            self.assertFalse(_command_matches_permanent_allowlist(bound))
        bound = self.command('meegle workitem create --project-key test')['args']['command']
        for suffix in ('; touch /tmp/unwanted', ' && id', ' | sh', '\nid', ' $(id)'):
            self.assertFalse(_command_matches_permanent_allowlist(bound + suffix))

    def test_missing_identity_or_grant_still_blocks(self):
        for speaker in ('', 'ou_other'):
            self.assertEqual(self.command('meegle workitem create --project-key test', speaker)['action'], 'block')
        self.assertEqual(self.command('meegle workitem create --project-key test; id')['action'], 'block')

    def test_rule_requires_request_and_readback(self):
        self.assertIn('不再以外部写入为由要求确认创建', policy.TURN_NOTE)
        self.assertIn('仅讨论方案或要求草稿不授权建单', policy.TURN_NOTE)
        self.assertIn('先查询核实，禁止直接重复创建', policy.TURN_NOTE)


if __name__ == '__main__':
    unittest.main()
