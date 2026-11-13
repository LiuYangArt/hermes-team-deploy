import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from event_service import Service, validate_event
from queue_store import QueueStore
from runner import notify, parse_result, related_ids, verify_target


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = QueueStore(Path(self.directory.name) / "queue.sqlite")

    def test_concurrent_duplicate_is_persisted_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.store.enqueue("one", {"id": 1}), range(30)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.get("one")["payload"], {"id": 1})

    def test_concurrent_claim_has_single_owner(self):
        for i in range(20):
            self.store.enqueue(str(i), {"id": i})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            rows = list(executor.map(lambda _: self.store.claim(), range(30)))
        keys = [r["key"] for r in rows if r]
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(set(keys)), 20)

    def test_restart_preserves_checkpoint_and_failed_records(self):
        self.store.enqueue("one", {})
        self.store.claim()
        self.store.checkpoint("one", {"target_id": "123"})
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.claim()["result"], {"target_id": "123"})
        self.store.fail("one", "offline")
        self.assertEqual(self.store.get("one")["status"], "failed")
        self.assertTrue(self.store.retry("one"))
        self.store.claim()
        self.store.finish("one", {"message_id": "receipt"})
        self.assertFalse(self.store.enqueue("one", {}))
        self.assertEqual(self.store.get("one")["status"], "done")


class EventTests(unittest.TestCase):
    def setUp(self):
        self.event = {"repository": "owner/repo", "number": 12,
                      "source_id": "123", "github_url": "https://github.com/owner/repo/issues/12"}
        self.config = {"routes": {"owner/repo": "chat"}}

    def test_only_allowlisted_repo_and_canonical_url(self):
        self.assertEqual(validate_event(self.event, self.config), self.event)
        for changes in ({"repository": "attacker/repo"}, {"number": True},
                        {"source_id": "123;command"}, {"github_url": "https://evil.test"}):
            with self.assertRaises(ValueError):
                validate_event(dict(self.event, **changes), self.config)

    def test_result_protocol_and_relations(self):
        self.assertEqual(related_ids([{"key": "456", "label": "Bug"}]), ["456"])
        self.assertEqual(related_ids([{"id": 456, "name": "Bug"}]), ["456"])
        self.assertEqual(related_ids({"key_label_value_list": [{"value": 456}]}), ["456"])
        self.assertEqual(parse_result('```json\n{"decision":"manual_review"}\n```')["decision"],
                         "manual_review")
        with self.assertRaises(ValueError):
            parse_result('{"decision":"success"}')

    def test_pool_link_or_unverified_owner_never_passes(self):
        config = {"simple_name": "demo", "feature_type": "feature"}
        result = {"decision": "classified", "classification": "bug", "target_id": "123"}
        with self.assertRaises(ValueError):
            verify_target(config, "creator", self.event, result)

    def target_fixture(self, kind="bug"):
        source = {"work_item_fields": [
            {"key": "field_3fba38", "value": self.event["github_url"]},
            {"key": "field_41cd35", "value": [{"id": 456}]},
            {"key": "field_f373bc", "value": [{"id": 456}]},
            {"key": "field_84351b",
             "value": "bgk7h9nl6" if kind == "bug" else "v8ywbsj8c"},
            {"key": "field_26717a",
             "value": "1sl5_gbh2" if kind == "bug" else "_amm6f04"},
        ]}
        target = {
            "work_item_attribute": {"work_item_type": {
                "key": "issue" if kind == "bug" else "feature"},
                "work_item_name": "中文标题"},
            "work_item_fields": [
                {"key": "description", "value": "中文说明 " + self.event["github_url"]},
                {"key": "watchers", "value": [{"key": "owner"}]},
                {"key": "field_94a30f", "value": [{"key": "follower"}]},
                {"key": "field_c81457", "value": [{"id": 123}]},
            ],
        }
        return source, target

    def test_verified_bug_rejects_wrong_person_missing_translation_or_source(self):
        config = {"simple_name": "demo", "feature_type": "feature"}
        result = {"decision": "classified", "classification": "bug", "target_id": "456",
                  "owner_userkeys": ["owner"], "follower_userkeys": ["follower"]}
        with patch("runner.get_item", side_effect=self.target_fixture()):
            self.assertTrue(verify_target(config, "creator", self.event, dict(result))["verified"])
        with patch("runner.get_item", side_effect=self.target_fixture()):
            with self.assertRaisesRegex(ValueError, "owner readback"):
                verify_target(config, "creator", self.event, dict(result, owner_userkeys=["wrong"]))
        source, target = self.target_fixture()
        target["work_item_attribute"]["work_item_name"] = "English title"
        target["work_item_fields"][0]["value"] = self.event["github_url"]
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "translation missing"):
                verify_target(config, "creator", self.event, dict(result))
        target["work_item_fields"][0]["value"] = "中文说明"
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "source link"):
                verify_target(config, "creator", self.event, dict(result))

    def test_feature_requires_bidirectional_source_relation(self):
        config = {"simple_name": "demo", "feature_type": "feature"}
        result = {"decision": "classified", "classification": "feature_request",
                  "target_id": "456"}
        with patch("runner.get_item", side_effect=self.target_fixture("feature")):
            self.assertTrue(verify_target(config, "creator", self.event, dict(result))["verified"])
        source, target = self.target_fixture("feature")
        target["work_item_fields"][-1]["value"] = []
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "Feature source relation"):
                verify_target(config, "creator", self.event, dict(result))

    def test_notification_retry_reads_saved_receipt_without_resending(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, review_chat="review", output_dir=directory)
            data = {"event": self.event, "result": {"decision": "manual_review", "reason": "check"},
                    "chat_id": "review", "uuid": "stable"}
            content = "待人工分诊 #12\n" + self.event["github_url"] + "\ncheck"
            response = {"data": {"messages": [{"message_id": "sent", "content": content}]}}
            with patch("runner.cli", side_effect=[
                    {"data": {"message_id": "sent"}}, RuntimeError("readback offline")]):
                with self.assertRaises(RuntimeError):
                    notify(config, data)
            with patch("runner.cli", return_value=response) as command:
                self.assertEqual(notify(config, data), {"message_id": "sent"})
                self.assertEqual(command.call_count, 1)
                self.assertIn("+messages-mget", command.call_args.args[0])

    def test_delivery_retry_uses_persisted_target(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(dict(self.config, state_dir=directory, runner_command=[],
                                   review_chat="review"))
            service.queue.enqueue("one", self.event)
            row = service.queue.claim()
            target = {"decision": "classified", "target_id": "456"}
            with patch.object(service, "command", side_effect=[target, RuntimeError("offline")]) as call:
                with self.assertRaises(RuntimeError):
                    service.process(row)
                self.assertEqual(call.call_count, 2)
            service.queue.fail("one", "offline")
            service.queue.retry("one")
            row = service.queue.claim()
            with patch.object(service, "command", return_value={"message_id": "receipt"}) as call:
                service.process(row)
                self.assertEqual(call.call_args.args[0], "notify")
                self.assertEqual(call.call_count, 1)
            self.assertEqual(service.queue.get("one")["result"]["message_id"], "receipt")
            service.lock.close()

    def test_http_ack_follows_commit_and_rejects_conflicting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, state_dir=directory, runner_command=[],
                          review_chat="review", token="test-secret")
            service = Service(config)
            server = ThreadingHTTPServer(("127.0.0.1", 0), service.handler())
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            self.addCleanup(service.lock.close)
            url = f"http://127.0.0.1:{server.server_port}/hooks/meegle-triage"

            def post(event, authenticated=True):
                headers = {"Content-Type": "application/json"}
                if authenticated:
                    headers["Authorization"] = "Bearer " + config["token"]
                return urlopen(Request(url, data=json.dumps(event).encode(), headers=headers))

            try:
                with self.assertRaises(HTTPError) as error:
                    post(self.event, False)
                self.assertEqual(error.exception.code, 401)
                with post(self.event) as response:
                    result = json.load(response)
                    self.assertEqual(response.status, 202)
                    self.assertIsNotNone(service.queue.get(result["key"]))
                with post(self.event) as response:
                    self.assertTrue(json.load(response)["duplicate"])
                with self.assertRaises(HTTPError) as error:
                    post(dict(self.event, source_id="999"))
                self.assertEqual(error.exception.code, 409)
                with self.assertRaises(HTTPError) as error:
                    post(dict(self.event, github_url="https://evil.test"))
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
