"""Routing decisions for the Lark topic extension. No live Feishu connection."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _routing():
    path = Path(__file__).resolve().parents[1] / "extensions" / "lark-topics" / "routing.py"
    spec = importlib.util.spec_from_file_location("lark_topics_routing", path)
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


routing = _routing()


class TopicRoutingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = routing.TopicStore(Path(self._tmp.name) / "topics.json")
        self.app = "app"
        self.chat = "oc_group"

    def tearDown(self):
        self._tmp.cleanup()

    def _route(self, **overrides):
        fields = dict(
            app_id=self.app, chat_id=self.chat, chat_type="group", message_id="om_first",
            thread_id=None, parent_id=None, root_id=None, sender_id="ou_a", mentioned=True,
        )
        fields.update(overrides)
        return self.store.route_inbound(**fields)

    def test_first_mention_and_followup_share_one_key(self):
        first = self._route()
        self.assertTrue(first.created)
        self.assertEqual(first.session_thread_id, "om_first")
        self.assertFalse(routing.is_server_thread_id(first.session_thread_id))

        created = self.store.plan_outbound(
            app_id=self.app, chat_id=self.chat, metadata_thread_id=first.session_thread_id, reply_to=None,
        )
        self.assertEqual(created.mode, "reply_in_thread")
        self.assertEqual(created.reply_to, "om_first")
        self.assertFalse(routing.is_server_thread_id(created.thread_id))

        self.assertTrue(self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_topic", root_id="om_first",
        ))
        follow = self._route(
            message_id="om_second", thread_id="omt_topic", mentioned=False, sender_id="ou_a",
        )
        self.assertEqual(follow.session_thread_id, "om_first")
        self.assertFalse(follow.created)

        later = self.store.plan_outbound(
            app_id=self.app, chat_id=self.chat, metadata_thread_id=follow.session_thread_id, reply_to="om_second",
        )
        self.assertEqual(later.mode, "server_thread")
        self.assertEqual(later.thread_id, "omt_topic")
        self.assertEqual(later.reply_to, "om_first")

    def test_two_mentions_stay_isolated(self):
        first = self._route(message_id="om_a", sender_id="ou_a")
        second = self._route(message_id="om_b", sender_id="ou_b")
        self.assertNotEqual(first.session_thread_id, second.session_thread_id)

    def test_restart_keeps_the_server_alias(self):
        self._route()
        self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_topic", root_id="om_first",
        )
        restored = routing.TopicStore(self.store.path)
        follow = restored.route_inbound(
            app_id=self.app, chat_id=self.chat, chat_type="group", message_id="om_later",
            thread_id="omt_topic", parent_id=None, root_id=None, sender_id="ou_a", mentioned=False,
        )
        self.assertEqual(follow.session_thread_id, "om_first")

    def test_quote_from_main_chat_rejoins_only_for_a_participant(self):
        self._route()
        self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_topic", root_id="om_first",
        )
        stranger = self._route(
            message_id="om_quote", thread_id=None, parent_id="om_bot", mentioned=False, sender_id="ou_stranger",
        )
        self.assertIsNone(stranger.session_thread_id)
        member = self._route(
            message_id="om_member_quote", thread_id=None, parent_id="om_bot", mentioned=False, sender_id="ou_a",
        )
        self.assertEqual(member.session_thread_id, "om_first")
        invited = self._route(
            message_id="om_invited", thread_id=None, parent_id="om_bot", mentioned=True, sender_id="ou_new",
        )
        self.assertEqual(invited.session_thread_id, "om_first")

    def test_plain_group_text_and_direct_messages_are_left_alone(self):
        plain = self._route(message_id="om_plain", mentioned=False)
        self.assertIsNone(plain.session_thread_id)
        direct = self._route(chat_type="p2p", message_id="om_dm")
        self.assertIsNone(direct.session_thread_id)
        official = self.store.plan_outbound(
            app_id=self.app, chat_id=self.chat, metadata_thread_id="omt_unknown", reply_to=None,
        )
        self.assertEqual(official.mode, "official")
        self.assertEqual(official.thread_id, "omt_unknown")

    def test_unmentioned_continuation_matches_admit(self):
        self._route()
        self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_topic", root_id="om_first",
        )
        self.assertTrue(self.store.allows_unmentioned(
            app_id=self.app, chat_id=self.chat, thread_id="omt_topic",
            parent_id=None, root_id=None, sender_id="ou_a",
        ))
        self.assertTrue(self.store.allows_unmentioned(
            app_id=self.app, chat_id=self.chat, thread_id="omt_topic",
            parent_id=None, root_id=None, sender_id="ou_stranger",
        ))
        self.assertTrue(self.store.allows_unmentioned(
            app_id=self.app, chat_id=self.chat, thread_id=None,
            parent_id="om_bot", root_id=None, sender_id="ou_a",
        ))

    def test_bind_refuses_a_different_topic_and_a_local_id(self):
        self._route()
        self.assertFalse(self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="om_not_a_thread", root_id="om_first",
        ))
        self.assertTrue(self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_one", root_id="om_first",
        ))
        self.assertFalse(self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_other", thread_id="omt_two", root_id="om_first",
        ))
        plan = self.store.plan_outbound(
            app_id=self.app, chat_id=self.chat, metadata_thread_id="om_first", reply_to=None,
        )
        self.assertEqual(plan.thread_id, "omt_one")

    def test_missing_anchor_fails_closed(self):
        self._route()
        topic = self.store.topic_for_alias(self.app, self.chat, "om_first")
        broken = routing.Topic(
            app_id=topic.app_id, chat_id=topic.chat_id, discussion_key=topic.discussion_key,
            anchor_message_id="", server_thread_id=None, root_id=None, participants=topic.participants,
        )
        self.store._topics[self.store._topic_storage_key(broken)] = broken
        plan = self.store.plan_outbound(
            app_id=self.app, chat_id=self.chat, metadata_thread_id="om_first", reply_to=None,
        )
        self.assertEqual(plan.mode, "fail")

    def test_other_people_are_remembered_until_someone_calls_the_bot(self):
        self._route()
        self.store.bind_server_topic(
            app_id=self.app, chat_id=self.chat, discussion_key="om_first",
            message_id="om_bot", thread_id="omt_topic", root_id="om_first",
        )
        aside = self._route(
            message_id="om_aside", thread_id="omt_topic", mentioned=False, sender_id="ou_b", text="问一下进度",
        )
        self.assertFalse(aside.deliver)
        topic = self.store.topic_for_alias(self.app, self.chat, "omt_topic")
        self.assertNotIn("ou_b", topic.participants)
        self.assertEqual(topic.active_sender, "ou_a")

        quiet = self._route(
            message_id="om_quiet", thread_id="omt_topic", mentioned=False, mentions_other=True,
            sender_id="ou_a", text="@同事 你看一下",
        )
        self.assertFalse(quiet.deliver)
        topic = self.store.topic_for_alias(self.app, self.chat, "omt_topic")
        self.assertEqual(topic.attention, "listening")

        both = self._route(
            message_id="om_both", thread_id="omt_topic", mentioned=True, mentions_other=True,
            sender_id="ou_b", text="@机器人 @同事 一起看",
        )
        self.assertTrue(both.deliver)
        self.assertIn("问一下进度", both.context)
        self.store.claim_speaker(self.app, self.chat, "om_first", "ou_b")
        topic = self.store.topic_for_alias(self.app, self.chat, "omt_topic")
        self.assertEqual(topic.attention, "talking")
        self.assertEqual(topic.active_sender, "ou_b")
        self.assertIn("ou_b", topic.participants)

    def test_restart_keeps_who_the_bot_is_talking_to(self):
        self._route()
        self._route(
            message_id="om_quiet", thread_id=None, parent_id="om_first", mentioned=False,
            mentions_other=True, sender_id="ou_a", text="@同事",
        )
        restored = routing.TopicStore(self.store.path)
        topic = restored.topic_for_alias(self.app, self.chat, "om_first")
        self.assertEqual(topic.attention, "listening")
        self.assertEqual(topic.active_sender, "ou_a")
        self.assertTrue(topic.heard)

    def test_response_fields_and_corrupt_store(self):
        message_id, thread_id, root_id = routing.thread_fields_from_response({
            "data": {"message_id": "om_bot", "thread_id": "omt_topic", "root_id": "om_first"},
        })
        self.assertEqual((message_id, thread_id, root_id), ("om_bot", "omt_topic", "om_first"))
        path = Path(self._tmp.name) / "broken.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(routing.TopicStoreError):
            routing.TopicStore(path)


if __name__ == "__main__":
    unittest.main()
