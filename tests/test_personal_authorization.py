"""Device flows never publish credentials before server identity verification."""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / 'extensions' / 'personal-auth'
package = types.ModuleType('auth_test_package')
package.__path__ = [str(EXT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location('auth_test_package.authorization', EXT / 'authorization.py')
auth = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auth
spec.loader.exec_module(auth)


class FakeCLI:
    def __init__(self):
        self.calls = []
        self.speaker = 'ou_a'
        self.email = 'a@example.com'
        self.pending = False

    def __call__(self, argv, env, cwd, **kwargs):
        self.calls.append((argv, env.copy(), cwd))
        if argv[1:3] == ['config', 'bind']:
            config = Path(env['LARKSUITE_CLI_CONFIG_DIR']) / 'hermes' / 'config.json'
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text('{"apps":[{"users":[]}]}')
        if '--no-wait' in argv or '--phase' in argv and 'init' in argv:
            return dict(device_code='NEVER-EXPOSE', client_id='CLIENT-PRIVATE', expires_in=600,
                        verification_url='https://example.com/lark', verification_uri_complete='https://example.com/meegle')
        if 'qrcode' in argv:
            (cwd / argv[-1]).write_bytes(b'png')
        if argv[1:3] == ['auth', 'status']:
            return {'identities': {'user': {'openId': self.speaker, 'verified': True}}}
        if argv[1] == 'api':
            return {'ok': True, 'data': {'open_id': self.speaker, 'email': 'a@example.com'}}
        if argv[1:3] == ['user', 'me']:
            return {'user_key': 'meegle_a', 'email': self.email}
        if argv[0] == 'meegle' and 'poll' in argv:
            if self.pending:
                return {'status': 'authorization_pending'}
            p = Path(env['HOME']) / '.meegle' / 'credentials.enc'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'credential')
            return {'status': 'ok'}
        if argv[0] == 'lark-cli' and '--device-code' in argv:
            p = Path(env['LARKSUITE_CLI_CONFIG_DIR']) / 'hermes' / 'config.json'
            p.write_text(json.dumps({'apps': [{'users': [{'userOpenId': self.speaker}]}]}))
        return {}


class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = FakeCLI()
        self.now = 1000
        self.broker = auth.Authorization(self.root, self.cli, lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def run_action(self, service, action, speaker='ou_a'):
        return self.broker.handle(speaker, service, action)

    def authorize_lark(self):
        self.assertTrue(self.run_action('lark', 'start')['ok'])
        self.assertEqual(self.run_action('lark', 'complete')['status'], 'authorized')

    def test_start_is_public_and_idempotent_but_never_contains_device_code(self):
        result = self.run_action('lark', 'start')
        self.assertNotIn('NEVER-EXPOSE', json.dumps(result))
        self.assertTrue(Path(result['qr_path']).is_file())
        count = len(self.cli.calls)
        self.assertEqual(result, self.run_action('lark', 'start'))
        self.assertEqual(len(self.cli.calls), count)
        self.assertFalse(auth.has_lark_grant(self.root, 'ou_a'))
        self.assertEqual(self.run_action('lark', 'complete', 'ou_b')['error'], 'no_pending')

    def test_matching_lark_identity_promotes_and_wrong_scan_is_discarded(self):
        self.authorize_lark()
        self.assertTrue(auth.has_lark_grant(self.root, 'ou_a'))
        self.run_action('lark', 'start', 'ou_b')
        result = self.run_action('lark', 'complete', 'ou_b')
        self.assertEqual(result['error'], 'identity_mismatch')
        self.assertFalse(auth.has_lark_grant(self.root, 'ou_b'))
        self.assertTrue(auth.has_lark_grant(self.root, 'ou_a'))

    def test_expiration_and_logout_only_affect_speaker(self):
        self.authorize_lark()
        self.run_action('lark', 'start', 'ou_b')
        self.now += 601
        self.assertEqual(self.run_action('lark', 'complete', 'ou_b')['error'], 'expired')
        self.run_action('lark', 'logout', 'ou_b')
        self.assertTrue(auth.has_lark_grant(self.root, 'ou_a'))
        self.run_action('lark', 'logout')
        self.assertFalse(auth.has_lark_grant(self.root, 'ou_a'))

    def test_meegle_must_match_verified_lark_email(self):
        self.assertEqual(self.run_action('meegle', 'start')['error'], 'lark_identity_required')
        self.authorize_lark()
        self.run_action('meegle', 'start')
        self.cli.pending = True
        self.assertEqual(self.run_action('meegle', 'complete')['error'], 'authorization_pending')
        self.cli.pending = False
        self.cli.email = 'other@example.com'
        self.assertEqual(self.run_action('meegle', 'complete')['error'], 'identity_mismatch')
        self.assertFalse(auth.has_meegle_grant(self.root, 'ou_a'))
        self.cli.email = 'A@example.com'
        self.run_action('meegle', 'start')
        self.assertEqual(self.run_action('meegle', 'complete')['status'], 'authorized')
        self.assertTrue(auth.has_meegle_grant(self.root, 'ou_a'))
        self.assertFalse(auth.has_meegle_grant(self.root, 'ou_b'))

    def test_failed_reauthorization_preserves_existing_grant(self):
        self.authorize_lark()
        self.run_action('lark', 'start')
        self.cli.speaker = 'ou_other'
        self.assertEqual(self.run_action('lark', 'complete')['error'], 'identity_mismatch')
        self.assertTrue(auth.has_lark_grant(self.root, 'ou_a'))

    def test_cli_secret_errors_are_not_returned(self):
        import subprocess
        from unittest.mock import patch
        with patch.object(auth.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'secret=NEVER-EXPOSE')):
            with self.assertRaises(auth.AuthError) as error:
                auth.run_cli(['lark-cli'], {}, self.root)
        self.assertNotIn('NEVER-EXPOSE', str(error.exception))

    def test_separate_token_stores_and_unknown_speaker(self):
        self.run_action('lark', 'start')
        self.run_action('lark', 'start', 'ou_b')
        dirs = {env['LARKSUITE_CLI_DATA_DIR'] for argv, env, cwd in self.cli.calls}
        self.assertEqual(len(dirs), 2)
        self.assertEqual(self.run_action('lark', 'start', '../other')['error'], 'unknown_speaker')


if __name__ == '__main__':
    unittest.main()
