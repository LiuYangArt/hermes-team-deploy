"""Focused tests for binding Lark approval cards to one gateway request."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "lark-topics"
PACKAGE = "lark_approval_adapter_test"


@dataclass
class SendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class FakeFeishuAdapter:
    def __init__(self, config):
        pass

    async def _send_interactive_card(
        self, chat_id, card, metadata, failure_message, *, state_map, state_id, session_key,
    ):
        state_map[state_id] = {"session_key": session_key, "chat_id": chat_id, "message_id": "card"}
        return SendResult(True, "card")

    @staticmethod
    def _card_response(card=None):
        return card

    def _validate_card_action(self, *, event, state, label, ident):
        actor = getattr(event.operator, "open_id", "")
        chat_id = getattr(event.context, "open_chat_id", "")
        if actor != "admin" or chat_id != state.get("chat_id"):
            return None
        return actor, chat_id, actor

    @staticmethod
    def _build_resolved_approval_card(*, choice, user_name):
        return {"resolved": choice, "user": user_name}


def load_adapter():
    package = ModuleType(PACKAGE)
    package.__path__ = [str(EXTENSION)]
    base = ModuleType("gateway.platforms.base")
    base.SendResult = SendResult
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: ROOT
    official = ModuleType("plugins.platforms.feishu.adapter")
    official.FeishuAdapter = FakeFeishuAdapter
    modules = {
        PACKAGE: package,
        "gateway": ModuleType("gateway"),
        "gateway.platforms": ModuleType("gateway.platforms"),
        "gateway.platforms.base": base,
        "hermes_constants": constants,
        "plugins": ModuleType("plugins"),
        "plugins.platforms": ModuleType("plugins.platforms"),
        "plugins.platforms.feishu": ModuleType("plugins.platforms.feishu"),
        "plugins.platforms.feishu.adapter": official,
    }
    modules["gateway"].__path__ = []
    modules["gateway.platforms"].__path__ = []
    modules["plugins"].__path__ = []
    modules["plugins.platforms"].__path__ = []
    modules["plugins.platforms.feishu"].__path__ = []
    with patch.dict(sys.modules, modules):
        for name in ("replies", "routing", "adapter"):
            spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", EXTENSION / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return sys.modules[f"{PACKAGE}.adapter"].LarkTopicAdapter


LarkTopicAdapter = load_adapter()


class LarkApprovalAdapterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = LarkTopicAdapter(object(), store=object())
        self.adapter._approval_state = {}
        self.adapter._app_id = "app"
        self.pending = []
        self.resolutions = []
        approval = ModuleType("tools.approval")

        def resolve(session_key, choice, request_id=None):
            self.resolutions.append((session_key, choice, request_id))
            matches = [item for item in self.pending if item.get("request_id") == request_id]
            if len(matches) != 1:
                return 0
            self.pending.remove(matches[0])
            return 1

        approval.resolve_gateway_approval = resolve
        tools = ModuleType("tools")
        tools.approval = approval
        self.modules = patch.dict(sys.modules, {
            "tools": tools, "tools.approval": approval,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_card_resolves_only_its_exact_request(self):
        self.pending = [{"request_id": "old"}, {"request_id": "current"}]
        result = await self._send("current")
        self.assertTrue(result.success)
        response = self._click("approve_once")
        self.assertEqual(response, {"resolved": "once", "user": "admin"})
        self.assertEqual(self.resolutions, [("session", "once", "current")])
        self.assertEqual([item["request_id"] for item in self.pending], ["old"])

    async def test_missing_request_id_fails_closed(self):
        self.pending = [{"request_id": "current"}]
        result = await self._send("")
        self.assertFalse(result.success)
        self.assertEqual(self.adapter._approval_state, {})

    async def test_expired_wrong_message_and_repeat_report_truthfully(self):
        self.pending = [{"request_id": "current"}]
        await self._send("current")
        wrong = self._click("approve_once", message="old-card")
        self.assertIn("本次点击没有批准或执行任何命令", wrong["elements"][0]["content"])
        self.assertEqual(self.resolutions, [])
        self.assertEqual(self._click("deny"), {"resolved": "deny", "user": "admin"})
        self.assertEqual(self._click("deny"), {"resolved": "deny", "user": "admin"})
        self.assertEqual(self.resolutions, [("session", "deny", "current")])

    async def test_processed_elsewhere_and_unauthorized_clicks_do_not_claim_approval(self):
        self.pending = [{"request_id": "current"}]
        await self._send("current")
        denied = self._click("approve_once", actor="other")
        self.assertIsNone(denied)
        self.assertEqual(self.resolutions, [])
        self.pending.clear()
        expired = self._click("approve_once")
        self.assertIn("本次点击没有批准或执行任何命令", expired["elements"][0]["content"])
        self.assertEqual(self.resolutions, [("session", "once", "current")])

    async def _send(self, request_id):
        metadata = {"exec_approval_request_id": request_id} if request_id else {}
        return await self.adapter._send_interactive_card(
            "chat", {}, metadata, "failed", state_map=self.adapter._approval_state,
            state_id=4, session_key="session",
        )

    def _click(self, action, *, message="card", actor="admin"):
        event = SimpleNamespace(
            operator=SimpleNamespace(open_id=actor),
            context=SimpleNamespace(open_chat_id="chat", open_message_id=message),
        )
        return self.adapter._handle_approval_card_action(
            event=event, action_value={"approval_id": 4, "hermes_action": action}, loop=None,
        )


if __name__ == "__main__":
    unittest.main()
