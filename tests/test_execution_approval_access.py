"""Run against the deployed Core dependencies to check approval entry points."""
import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_package(name, directory):
    spec = importlib.util.spec_from_file_location(name, directory / '__init__.py', submodule_search_locations=[str(directory)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ApprovalAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gateway.platforms.base
            import tools.approval
        except ImportError as exc:
            raise unittest.SkipTest('Requires deployed Hermes runtime: ' + str(exc))
        cls.access = load_package('approval_access_test', ROOT / 'extensions/lark-access')
        load_package('approval_topics_test', ROOT / 'extensions/lark-topics')
        cls.adapter_module = __import__('approval_topics_test.adapter', fromlist=['LarkTopicAdapter'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        folder = self.root / 'lark-access'
        folder.mkdir()
        (folder / 'config.json').write_text(json.dumps({'auto_enroll': False, 'admins': ['ou_admin']}))
        self.adapter = self.adapter_module.LarkTopicAdapter.__new__(self.adapter_module.LarkTopicAdapter)
        self.adapter._admins = {'ou_other'}
        self.adapter._allowed_group_users = {'*'}
        self.adapter._get_cached_sender_name = lambda value: value
        self.addCleanup(self.tmp.cleanup)

    def test_card_ignores_chat_allowlist_and_rechecks_admin_on_resolution(self):
        with patch.object(self.adapter_module, 'get_hermes_home', return_value=self.root):
            for actor, allowed in [('ou_admin', True), ('ou_other', False), ('', False)]:
                event = SimpleNamespace(operator=SimpleNamespace(open_id=actor), context=SimpleNamespace(open_chat_id='chat'))
                state = {'chat_id': 'chat', 'session_key': 'session'}
                checked = self.adapter._validate_card_action(event=event, state=state, label='approval', ident=1)
                self.assertEqual(checked is not None, allowed)
                states = {1: state}
                popped = self.adapter._pop_validated_prompt_state(states=states, ident=1, label='Approval', open_id=actor, chat_id='chat', unauthorized_fmt='%s %s', operator_repr=actor)
                self.assertEqual(popped is not None, allowed)
                self.assertEqual(1 in states, not allowed)

    def test_missing_admin_config_denies_cards(self):
        (self.root / 'lark-access/config.json').unlink()
        with patch.object(self.adapter_module, 'get_hermes_home', return_value=self.root):
            self.assertFalse(self.adapter._is_interactive_operator_authorized('ou_admin'))

    def test_text_approval_is_gated_but_normal_answers_and_personal_requests_pass(self):
        for actor, text, pending, blocked in [
            ('ou_other', '/approve always', True, True),
            ('ou_other', '/deny', True, True),
            ('ou_other', 'yes', True, True),
            ('ou_other', 'yes', False, False),
            ('ou_other', '查看我的任务', False, False),
            ('ou_other', '已授权', False, False),
            ('ou_admin', '/approve', True, False),
        ]:
            with self.subTest(actor=actor, text=text, pending=pending):
                send = AsyncMock()
                source = SimpleNamespace(platform='feishu', user_id=actor, user_id_alt='', user_name='test', is_bot=False, chat_id='chat', thread_id='thread')
                event = SimpleNamespace(source=source, text=text, message_id='message', raw_message=None, get_command=lambda: text[1:].split()[0] if text.startswith('/') else None)
                gateway = SimpleNamespace(_session_key_for_source=lambda _: 'session', _plaintext_approval_words=lambda: {'yes': ('approve','')}, _delivery_adapter_for=lambda _: SimpleNamespace(send=send))
                with patch.object(self.access, '_home', return_value=self.root), patch('tools.approval.has_blocking_approval', return_value=pending):
                    result = asyncio.run(self.access._on_inbound(event=event, gateway=gateway))
                self.assertEqual(bool(result and result.get('action') == 'skip'), blocked)
                self.assertEqual(send.await_count, int(blocked))
                if blocked:
                    self.assertEqual(send.call_args.kwargs['metadata'], {'thread_id': 'thread'})


if __name__ == '__main__':
    unittest.main()
