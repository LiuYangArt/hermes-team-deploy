"""Concurrency tests for the Lark single-reply adapter seam."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "lark-topics"
PACKAGE = "lark_reply_adapter_test"


@dataclass
class SendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class FakeFeishuAdapter:
    MAX_MESSAGE_LENGTH = 8000

    def __init__(self, config):
        self.created = []
        self.edited = []
        self.completions = []
        self.send_results = []
        self.edit_results = []
        self.send_entered = None
        self.send_release = None
        self.edit_entered = None
        self.edit_release = None

    def format_message(self, content):
        return content

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.created.append((chat_id, content, reply_to, metadata))
        if self.send_entered is not None:
            self.send_entered.set()
        if self.send_release is not None:
            await self.send_release.wait()
        if self.send_results:
            return self.send_results.pop(0)
        return SendResult(True, f"om_created_{len(self.created)}")

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        self.edited.append((chat_id, message_id, content, finalize))
        if self.edit_entered is not None:
            self.edit_entered.set()
        if self.edit_release is not None:
            await self.edit_release.wait()
        if self.edit_results:
            return self.edit_results.pop(0)
        return SendResult(True, message_id)

    async def on_processing_complete(self, event, outcome):
        self.completions.append((event, outcome))


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
    base.SendResult = SendResult
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
replies = adapter_module.replies
LarkTopicAdapter = adapter_module.LarkTopicAdapter


class LarkReplyAdapterTest(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        return LarkTopicAdapter(object(), store=object())

    async def test_concurrent_first_progress_creates_once_then_edits(self):
        adapter = self.adapter()
        adapter.send_entered = asyncio.Event()
        adapter.send_release = asyncio.Event()
        replies.begin("event")

        first = asyncio.create_task(adapter.send("chat", "skill one"))
        await adapter.send_entered.wait()
        second = asyncio.create_task(adapter.send("chat", "skill two"))
        adapter.send_release.set()
        await asyncio.gather(first, second)

        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.edited), 1)
        self.assertEqual(adapter.edited[0][2], "skill two")

    async def test_progress_create_and_final_are_serialized_into_one_message(self):
        adapter = self.adapter()
        adapter.send_entered = asyncio.Event()
        adapter.send_release = asyncio.Event()
        replies.begin("event")

        progress = asyncio.create_task(adapter.send("chat", "reading skill"))
        await adapter.send_entered.wait()
        final = asyncio.create_task(adapter.send("chat", "done", metadata={"notify": True}))
        adapter.send_release.set()
        await asyncio.gather(progress, final)

        self.assertEqual(len(adapter.created), 1)
        self.assertEqual([(item[2], item[3]) for item in adapter.edited], [("done", True)])

    async def test_late_progress_waiting_on_final_cannot_overwrite_it(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "reading skill")
        adapter.edit_entered = asyncio.Event()
        adapter.edit_release = asyncio.Event()

        final = asyncio.create_task(adapter.send("chat", "answer", metadata={"notify": True}))
        await adapter.edit_entered.wait()
        late = asyncio.create_task(adapter.send("chat", "late progress"))
        adapter.edit_release.set()
        final_result, late_result = await asyncio.gather(final, late)

        self.assertTrue(final_result.success)
        self.assertTrue(late_result.success)
        self.assertEqual([item[2] for item in adapter.edited], ["answer"])

    async def test_concurrent_turns_keep_separate_reply_messages(self):
        adapter = self.adapter()

        async def run_turn(event_id, progress, final):
            replies.begin(event_id)
            await adapter.send("chat", progress)
            await asyncio.sleep(0)
            await adapter.send("chat", final, metadata={"notify": True})

        await asyncio.gather(
            run_turn("event-a", "progress-a", "final-a"),
            run_turn("event-b", "progress-b", "final-b"),
        )

        message_by_progress = {item[1]: f"om_created_{index}" for index, item in enumerate(adapter.created, 1)}
        self.assertEqual(
            {(item[1], item[2]) for item in adapter.edited},
            {(message_by_progress["progress-a"], "final-a"), (message_by_progress["progress-b"], "final-b")},
        )

    async def test_close_waits_for_inflight_edit_then_finishes_same_message(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "started")
        adapter.edit_entered = asyncio.Event()
        adapter.edit_release = asyncio.Event()
        event = types.SimpleNamespace(message_id="event", source=types.SimpleNamespace(chat_id="chat"))

        progress = asyncio.create_task(adapter.send("chat", "working"))
        await adapter.edit_entered.wait()
        close = asyncio.create_task(adapter.on_processing_complete(event, "failure"))
        adapter.edit_release.set()
        await asyncio.gather(progress, close)

        self.assertEqual([item[2] for item in adapter.edited], ["working", replies.FAILURE_TEXT])
        self.assertEqual([item[3] for item in adapter.edited], [False, True])
        self.assertEqual(len(adapter.completions), 1)

    async def test_failed_first_send_can_be_retried(self):
        adapter = self.adapter()
        adapter.send_results = [SendResult(False, error="temporary"), SendResult(True, "om_retry")]
        replies.begin("event")

        failed = await adapter.send("chat", "first")
        retried = await adapter.send("chat", "second")

        self.assertFalse(failed.success)
        self.assertTrue(retried.success)
        self.assertEqual(len(adapter.created), 2)
        self.assertEqual(len(adapter.edited), 0)

    async def test_failed_progress_edit_does_not_create_another_message(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "first")
        adapter.edit_results = [SendResult(False, error="temporary")]

        result = await adapter.send("chat", "second")

        self.assertFalse(result.success)
        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.edited), 1)

    async def test_long_first_progress_is_clipped_without_extra_message(self):
        adapter = self.adapter()
        adapter.MAX_MESSAGE_LENGTH = 4
        replies.begin("event")

        await adapter.send("chat", "abcdef")

        self.assertEqual([item[1] for item in adapter.created], ["cdef"])
        self.assertEqual(adapter.edited, [])

    async def test_progress_edits_and_direct_edits_share_eighteen_edit_budget(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "start")

        for index in range(18):
            await adapter.send("chat", f"progress-{index}")
        await adapter.edit_message("chat", "om_created_1", "direct")
        await adapter.send("chat", "over-budget")
        final = await adapter.send("chat", "final answer", metadata={"notify": True})

        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.edited[:-1]), 18)
        self.assertEqual(adapter.edited[-1][1:4], ("om_created_1", "final answer", True))
        self.assertEqual(final.message_id, "om_created_1")

    async def test_repeated_identical_progress_does_not_consume_edit_budget(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "start")
        for _ in range(30):
            await adapter.send("chat", "same")

        self.assertEqual(len(adapter.edited), 1)
        self.assertEqual(replies.current().progress_edits, 1)

    async def test_segment_finalize_keeps_lane_open_until_processing_success(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "start")
        await adapter.edit_message("chat", "om_created_1", "preview", finalize=True)
        await adapter.send("chat", "after segment")

        self.assertEqual([item[2] for item in adapter.edited], ["preview", "after segment"])
        event = types.SimpleNamespace(message_id="event", source=types.SimpleNamespace(chat_id="chat", thread_id="thread"))
        await adapter.on_processing_complete(event, "success")

        self.assertEqual([item[2] for item in adapter.edited], ["preview", "after segment", "preview"])
        self.assertTrue(replies.current().delivered)

    async def test_cancelled_first_send_is_settled_without_duplicate_cleanup_create(self):
        adapter = self.adapter()
        adapter.send_entered = asyncio.Event()
        adapter.send_release = asyncio.Event()
        replies.begin("event")
        sending = asyncio.create_task(adapter.send("chat", "starting"))
        await adapter.send_entered.wait()
        sending.cancel()
        adapter.send_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await sending

        event = types.SimpleNamespace(message_id="event", source=types.SimpleNamespace(chat_id="chat"))
        await adapter.on_processing_complete(event, "cancelled")

        self.assertEqual(len(adapter.created), 1)
        self.assertEqual([item[2] for item in adapter.edited], [replies.CANCELLED_TEXT])

    async def test_failed_cleanup_edit_still_closes_progress_lane(self):
        adapter = self.adapter()
        replies.begin("event")
        await adapter.send("chat", "starting")
        adapter.edit_results = [SendResult(False, error="cleanup failed")]
        event = types.SimpleNamespace(message_id="event", source=types.SimpleNamespace(chat_id="chat"))

        await adapter.on_processing_complete(event, "cancelled")
        result = await adapter.send("chat", "late progress")

        self.assertTrue(adapter.edited[0][3])
        self.assertEqual(len(adapter.created), 1)
        self.assertEqual(len(adapter.edited), 1)
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
