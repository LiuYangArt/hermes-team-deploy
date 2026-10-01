"""Progress and final answers share one message. No live Feishu connection."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


def _replies():
    path = Path(__file__).resolve().parents[1] / "extensions" / "lark-topics" / "replies.py"
    spec = importlib.util.spec_from_file_location("lark_topic_replies", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


replies = _replies()


class ReplyReuseTest(unittest.TestCase):
    def setUp(self):
        replies.begin("om_request")

    def test_final_reuses_the_progress_message(self):
        self.assertEqual(replies.decide_send({"thread_id": "omt_a"}), "create")
        replies.note_created("om_progress")
        self.assertEqual(replies.decide_send({"notify": True, "thread_id": "omt_a"}), "edit")
        replies.note_delivered()
        self.assertEqual(replies.decide_send({"thread_id": "omt_a"}), "ignore")
        self.assertEqual(replies.decide_edit("om_progress"), "ignore")

    def test_no_progress_sends_a_normal_final(self):
        self.assertEqual(replies.decide_send({"notify": True}), "pass")

    def test_late_progress_after_close_is_ignored(self):
        replies.note_created("om_progress")
        self.assertEqual(replies.closing_text("om_request", "cancelled"), replies.CANCELLED_TEXT)
        self.assertEqual(replies.decide_edit("om_progress"), "ignore")
        self.assertEqual(replies.decide_send({"thread_id": "omt_a"}), "ignore")

    def test_failure_replaces_unfinished_progress(self):
        replies.note_created("om_progress")
        self.assertEqual(replies.closing_text("om_request", "failure"), replies.FAILURE_TEXT)

    def test_success_does_not_overwrite_a_delivered_answer(self):
        replies.note_created("om_progress")
        replies.note_delivered()
        self.assertIsNone(replies.closing_text("om_request", "cancelled"))

    def test_another_turn_does_not_see_this_message(self):
        replies.note_created("om_progress")

        async def other():
            replies.begin("om_other")
            return replies.decide_send({"notify": True}), replies.current().message_id

        decision, message_id = asyncio.run(other())
        self.assertEqual(decision, "pass")
        self.assertIsNone(message_id)
        self.assertEqual(replies.current().message_id, "om_progress")

    def test_long_progress_stays_one_clip_and_final_splits(self):
        self.assertEqual(replies.clip_progress("abcdef", 4), "cdef")
        self.assertEqual(replies.split_final("abcdef", 4), ["abcd", "ef"])

    def test_missing_attachment_is_explained(self):
        self.assertEqual(replies.attachment_note(2, 1), replies.UNREADABLE_ATTACHMENT)
        self.assertIsNone(replies.attachment_note(1, 1))
        self.assertIsNone(replies.attachment_note(0, 0))


if __name__ == "__main__":
    unittest.main()
