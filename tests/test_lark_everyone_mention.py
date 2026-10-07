"""@所有人 must not wake the bot unless the same message also @s the bot."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "lark-topics"
PACKAGE = "lark_everyone_mention_test"


class FakeFeishuAdapter:
    def __init__(self, config):
        self.super_calls = 0
        self.super_result = None
        self.processed = []

    async def _process_inbound_message(self, **kwargs):
        self.processed.append(kwargs)

    def _admit(self, sender, message):
        self.super_calls += 1
        return self.super_result

    def _message_mentions_bot(self, mentions):
        return any(getattr(item, "name", None) == "Hermi" for item in mentions)

    def _normalize(self, message_type, raw, mentions):
        return SimpleNamespace(mentions=[])

    def _post_mentions_bot(self, mentions):
        return False

    def _mentions_self(self, message):
        raw = getattr(message, "content", "") or ""
        return "@_all" in raw


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_adapter():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(EXTENSION)]
    gateway = types.ModuleType("gateway")
    gateway.__path__ = []
    platforms = types.ModuleType("gateway.platforms")
    platforms.__path__ = []
    base = types.ModuleType("gateway.platforms.base")
    base.SendResult = object
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: ROOT
    plugins = types.ModuleType("plugins")
    plugins.__path__ = []
    plugin_platforms = types.ModuleType("plugins.platforms")
    plugin_platforms.__path__ = []
    feishu = types.ModuleType("plugins.platforms.feishu")
    feishu.__path__ = []
    official = types.ModuleType("plugins.platforms.feishu.adapter")
    official.FeishuAdapter = FakeFeishuAdapter
    modules = {
        PACKAGE: package,
        "gateway": gateway,
        "gateway.platforms": platforms,
        "gateway.platforms.base": base,
        "hermes_constants": constants,
        "plugins": plugins,
        "plugins.platforms": plugin_platforms,
        "plugins.platforms.feishu": feishu,
        "plugins.platforms.feishu.adapter": official,
    }
    with patch.dict(sys.modules, modules):
        _load_module(f"{PACKAGE}.replies", EXTENSION / "replies.py")
        _load_module(f"{PACKAGE}.routing", EXTENSION / "routing.py")
        return _load_module(f"{PACKAGE}.adapter", EXTENSION / "adapter.py")


adapter_module = _load_adapter()
LarkTopicAdapter = adapter_module.LarkTopicAdapter


def _everyone():
    return SimpleNamespace(key="@_all", name="所有人", id=SimpleNamespace(open_id="", user_id=""))


def _bot():
    return SimpleNamespace(key="@_user_1", name="Hermi", id=SimpleNamespace(open_id="ou_bot", user_id=""))


def _message(content, mentions, chat_type="group"):
    return SimpleNamespace(
        chat_type=chat_type, chat_id="oc_group", content=content, mentions=mentions,
        message_type="text", thread_id="omt_topic", parent_id=None, root_id=None,
    )


class EveryoneMentionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LarkTopicAdapter(object(), store=SimpleNamespace())
        self.adapter._app_id = "app"
        self.continued = []
        self.adapter._topics.allows_unmentioned = lambda **kwargs: self.continued.append(kwargs) or True

    def test_everyone_only_is_dropped_before_topic_continuation(self):
        message = _message('{"text":"@_all 开会"}', [_everyone()])
        reason = self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message)
        self.assertEqual(reason, "everyone_not_mentioned")
        self.assertEqual(self.adapter.super_calls, 0)
        self.assertEqual(self.continued, [])
        self.assertFalse(self.adapter._mentions_self(message))

    def test_everyone_in_mentions_without_text_placeholder_is_dropped(self):
        message = _message('{"text":"开会"}', [_everyone()])
        self.assertEqual(
            self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message),
            "everyone_not_mentioned",
        )
        self.assertEqual(self.adapter.super_calls, 0)

    def test_post_everyone_tag_is_dropped(self):
        content = '{"zh_cn":{"content":[[{"tag":"at","user_id":"@_all"}]]}}'
        message = _message(content, [])
        self.assertEqual(
            self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message),
            "everyone_not_mentioned",
        )

    def test_typed_words_are_not_an_everyone_mention(self):
        message = _message('{"text":"@所有人 开会"}', [])
        self.assertIsNone(self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message))
        self.assertEqual(self.adapter.super_calls, 1)

    def test_everyone_plus_bot_still_passes(self):
        message = _message('{"text":"@_all @_user_1 看一下"}', [_everyone(), _bot()])
        self.assertTrue(self.adapter._mentions_self(message))
        self.assertIsNone(self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message))
        self.assertEqual(self.adapter.super_calls, 1)
        self.assertEqual(self.continued, [])

    def test_everyone_named_like_the_bot_does_not_count(self):
        lookalike = SimpleNamespace(key="@_all", name="Hermi", id=SimpleNamespace(open_id="", user_id=""))
        message = _message('{"text":"@_all"}', [lookalike])
        self.assertEqual(
            self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message),
            "everyone_not_mentioned",
        )

    def test_direct_message_is_not_blocked_by_the_placeholder(self):
        message = _message('{"text":"@_all"}', [_everyone()], chat_type="p2p")
        self.assertIsNone(self.adapter._admit(SimpleNamespace(sender_id="ou_a"), message))
        self.assertEqual(self.adapter.super_calls, 1)

    async def test_processing_never_routes_everyone_only(self):
        routed = []
        self.adapter._topics.route_inbound = lambda **kwargs: routed.append(kwargs)
        message = _message('{"text":"@_all 开会"}', [_everyone()])
        await self.adapter._process_inbound_message(
            data=None, message=message, sender_id="ou_a", chat_type="group", message_id="om_all",
        )
        self.assertEqual(routed, [])
        self.assertEqual(self.adapter.processed, [])

    async def test_processing_routes_everyone_plus_bot(self):
        routed = []

        def route(**kwargs):
            routed.append(kwargs)
            return SimpleNamespace(deliver=True, discussion_key=None, context=None, session_thread_id=None)

        self.adapter._topics.route_inbound = route
        self.adapter._mentions_other = lambda message: True
        message = _message('{"text":"@_all @_user_1 看一下"}', [_everyone(), _bot()])
        await self.adapter._process_inbound_message(
            data=None, message=message, sender_id="ou_a", chat_type="group", message_id="om_both",
        )
        self.assertEqual(len(routed), 1)
        self.assertTrue(routed[0]["mentioned"])
        self.assertEqual(len(self.adapter.processed), 1)
