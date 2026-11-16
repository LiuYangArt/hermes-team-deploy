import concurrent.futures
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from types import SimpleNamespace

from event_service import Service, validate_event
from queue_store import QueueStore
from runner import (cli, create_placeholder, ensure_target, event_path, link_placeholder,
                    find_created_target, find_source_target, notify, parse_result,
                    related_ids, retain_people, save_json, seed_result, triage,
                    verify_admission, verify_pm, verify_target)


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
            rows = list(executor.map(lambda _: self.store.claim("admission"), range(30)))
        keys = [r["key"] for r in rows if r]
        self.assertEqual(len(keys), 20)
        self.assertEqual(len(set(keys)), 20)

    def test_restart_preserves_checkpoint_and_failed_records(self):
        self.store.enqueue("one", {})
        self.store.claim("admission")
        self.store.checkpoint("one", {"target_id": "123"})
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.claim("triage")["result"], {"target_id": "123"})
        self.store.fail("one", "offline")
        self.assertEqual(self.store.get("one")["status"], "failed")
        self.assertTrue(self.store.retry("one"))
        self.assertIsNotNone(self.store.claim("triage"))
        self.store.finish("one", {"message_id": "receipt"})
        self.assertFalse(self.store.enqueue("one", {}))
        self.assertEqual(self.store.get("one")["status"], "done")

    def test_admission_keeps_retrying_after_three_failures(self):
        self.store.enqueue("one", {})
        for _ in range(4):
            self.assertIsNotNone(self.store.claim("admission"))
            self.store.fail("one", "Meegle temporarily unavailable")
            with self.store.connection() as db:
                db.execute("UPDATE events SET updated=0 WHERE key='one'")
            self.assertEqual(self.store.retry_transient(), 1)
        self.assertEqual(self.store.claim("admission")["attempts"], 5)
        self.assertEqual(self.store.get("one")["status"], "processing")


class EventTests(unittest.TestCase):
    def setUp(self):
        self.event = {"repository": "owner/repo", "number": 12,
                      "source_id": "123", "github_url": "https://github.com/owner/repo/issues/12"}
        self.config = {"repositories": ["owner/repo"], "notification_chat": "chat",
                       "pm_mention_user_id": "ou_pm"}

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
        self.assertEqual(parse_result(
            '```json\n{"decision":"manual_review","target_id":"456"}\n```')["decision"],
                         "manual_review")
        with self.assertRaises(ValueError):
            parse_result('{"decision":"success"}')
        with self.assertRaisesRegex(ValueError, "final target"):
            parse_result('{"decision":"manual_review","reason":"unknown owner"}')
        with self.assertRaisesRegex(RuntimeError, "network error"):
            parse_result('{"decision":"failed","reason":"network error"}')

    def admission_config(self, directory):
        return dict(self.config, job_id="job", project_key="project", simple_name="demo",
                    source_type="pool", feature_type="feature", output_dir=directory,
                    prompt_path=str(Path(directory) / "prompt.txt"), review_source_status="review",
                    pm_follower_userkey="321", pm_email="pm@example.com", bug_template_id="10",
                    feature_request_template_id="11")

    def source_fixture(self):
        return {"work_item_attribute": {
            "work_item_type": {"key": "pool"}, "work_item_name": "English report"},
            "work_item_fields": [
                {"key": "description", "value": "details <img src=\"https://image.test/x\">"},
                {"key": "field_3fba38", "value": self.event["github_url"]},
                {"key": "field_f3cd11", "value": self.event["repository"]},
                {"key": "field_7531ae", "value": self.event["number"]},
                {"key": "field_41cd35", "value": []},
                {"key": "field_f373bc", "value": []}]}

    def test_model_returned_failure_and_exception_both_keep_verified_target(self):
        for failure in (False, True):
            with self.subTest(exception=failure), tempfile.TemporaryDirectory() as directory:
                config = self.admission_config(directory)
                Path(config["prompt_path"]).write_text("prompt")
                admitted = {"source_id": "123", "classification": "bug", "target_id": "456",
                            "phase": "admitted", "admitted": True}
                result = dict(admitted, decision="manual_review", owner_userkeys=[],
                              follower_userkeys=["321"], verified=True)
                def model(*args, **kwargs):
                    if failure:
                        raise RuntimeError("model import or transport crashed")
                    return False, "log", "", "model unavailable"
                with patch.dict("sys.modules", {
                        "cron.jobs": SimpleNamespace(get_job=lambda _: {"personal_creator": "creator"}),
                        "cron.scheduler": SimpleNamespace(run_job=model)}), \
                        patch("runner.verify_admission", return_value=admitted), \
                        patch("runner.seed_result", return_value=result) as seed, \
                        patch("runner.retain_people"), \
                        patch("runner.create_placeholder") as create:
                    with self.assertRaisesRegex(RuntimeError, "正式单已入库"):
                        triage(config, self.event, admitted)
                create.assert_not_called()
                self.assertEqual(seed.call_count, 1)
                self.assertFalse(event_path(config, self.event).exists())

    def test_removed_existing_people_are_restored_and_cannot_pass(self):
        config = {"project_key": "project"}
        admitted = {"target_id": "456", "classification": "bug"}
        item = {"work_item_fields": [
            {"key": "watchers", "value": [{"key": "new-owner"}]},
            {"key": "field_94a30f", "value": [{"key": "pm"}]}]}
        with patch("runner.get_item", return_value=item), patch("runner.update_item") as update:
            with self.assertRaisesRegex(ValueError, "Existing people"):
                retain_people(config, "creator", admitted,
                              {"owner_userkeys": ["human"], "follower_userkeys": ["pm", "human"]})
        self.assertEqual(update.call_args.args[3], [
            {"field_key": "watchers", "field_value": '["human", "new-owner"]'},
            {"field_key": "field_94a30f", "field_value": '["human", "pm"]'}])

    def test_admission_persists_id_before_readback_and_recovers_without_create(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.admission_config(directory)
            with patch.dict("sys.modules", {"cron.jobs": SimpleNamespace(
                    get_job=lambda _: {"personal_creator": "creator"})}), \
                    patch("runner.get_item", return_value=self.source_fixture()), \
                    patch("runner.find_source_target", return_value=None), \
                    patch("runner.create_placeholder", return_value="456") as create, \
                    patch("runner.verify_admission", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    ensure_target(config, self.event)
                self.assertEqual(json.loads(event_path(config, self.event, "target-").read_text())["target_id"], "456")
                create.assert_called_once()
            with patch.dict("sys.modules", {"cron.jobs": SimpleNamespace(
                    get_job=lambda _: {"personal_creator": "creator"})}), \
                    patch("runner.get_item") as get, patch("runner.create_placeholder") as create, \
                    patch("runner.verify_admission", side_effect=lambda c, cr, e, r: dict(r, admitted=True)):
                value = ensure_target(config, self.event)
            self.assertEqual(value["target_id"], "456")
            get.assert_not_called()
            create.assert_not_called()

    def test_create_timeout_reconciles_instead_of_creating_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.admission_config(directory)
            journal = {"classification": "bug"}
            with patch("runner.cli", side_effect=TimeoutError("response lost")):
                with self.assertRaises(TimeoutError):
                    create_placeholder(config, "creator", self.event, self.source_fixture(), journal)
            saved = json.loads(event_path(config, self.event, "target-").read_text())
            self.assertTrue(saved["create_started"])
            with patch("runner.find_created_target", return_value="456"), patch("runner.cli") as create:
                self.assertEqual(create_placeholder(config, "creator", self.event,
                                 self.source_fixture(), saved), "456")
                create.assert_not_called()
            with patch("runner.find_created_target", return_value=None), patch("runner.cli") as create:
                with self.assertRaisesRegex(RuntimeError, "回执未知"):
                    create_placeholder(config, "creator", self.event, self.source_fixture(), saved)
                create.assert_not_called()
            saved["create_started"] -= 301
            with patch("runner.find_created_target", return_value=None), \
                    patch("runner.cli", return_value={"work_item_id": "789"}) as create:
                self.assertEqual(create_placeholder(config, "creator", self.event,
                                 self.source_fixture(), saved), "789")
                create.assert_called_once()

    def test_placeholder_creation_needs_no_model_or_live_pm_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.admission_config(directory)
            with patch("runner.cli", return_value={"data": {"work_item_id": "789"}}) as command:
                ident = create_placeholder(config, "creator", self.event,
                                           self.source_fixture(), {"classification": "bug"})
            self.assertEqual(ident, "789")
            args = command.call_args.args[0]
            params = json.loads(args[args.index("--params") + 1])
            self.assertEqual(params["work_item_type"], "issue")
            self.assertIn(self.event["github_url"], params["fields"][2]["field_value"])
            self.assertNotIn("<img", params["fields"][2]["field_value"])
            self.assertNotIn("field_94a30f", [x["field_key"] for x in params["fields"]])
            self.assertEqual(command.call_count, 1)

    def test_query_contract_handles_empty_results_escaping_and_ambiguity(self):
        config = {"project_key": "project", "simple_name": "demo"}
        def response(ids, count=None):
            return {"list": [{"count": len(ids) if count is None else count}],
                    "data": {"1": [{"moql_field_list": [
                        {"key": "work_item_id", "value": {"long_value": ident}}]}
                        for ident in ids]}}
        with patch("runner.cli", return_value={"list": None, "data": {}}):
            self.assertIsNone(find_created_target(config, "creator", "x", "bug"))
        with patch("runner.cli", return_value=response([456])) as command:
            self.assertEqual(find_created_target(config, "creator", "user's report", "bug"), "456")
            args = command.call_args.args[0]
            self.assertIn("user''s report", json.loads(args[args.index("--params") + 1])["mql"])
        for value in (response([456, 789]), response([456], 2)):
            with patch("runner.cli", return_value=value):
                with self.assertRaisesRegex(ValueError, "Multiple"):
                    find_created_target(config, "creator", "x", "bug")

    def test_source_lookup_reuses_feature_but_not_issue_number_prefix(self):
        config = {"project_key": "project", "simple_name": "demo", "feature_type": "feature"}
        hit = {"data": {"1": [{"moql_field_list": [
            {"key": "work_item_id", "value": {"long_value": 456}}]}]},
            "list": [{"count": 1}]}
        description = lambda url: {"work_item_fields": [{"key": "description", "value": url}]}
        with patch("runner.cli", side_effect=[hit, hit]), \
                patch("runner.get_item", side_effect=[
                    description(self.event["github_url"] + "3"),
                    description(self.event["github_url"])]):
            self.assertEqual(find_source_target(config, "creator", self.event),
                             ("456", "feature_request"))
        with patch("runner.cli", side_effect=[hit, hit]), \
                patch("runner.get_item", return_value=description(self.event["github_url"])):
            with self.assertRaisesRegex(ValueError, "Multiple"):
                find_source_target(config, "creator", self.event)

    def test_admission_repairs_missing_source_link_without_erasing_description(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.admission_config(directory)
            _, target = self.target_fixture()
            target["work_item_fields"][0]["value"] = "原有人工正文"
            repaired = dict(target, work_item_fields=[
                {"key": "description", "value": "原有人工正文\n" + self.event["github_url"]}])
            with patch("runner.get_item", side_effect=[target, repaired]), \
                    patch("runner.update_item") as update:
                result = verify_admission(config, "creator", self.event,
                                          {"target_id": "456", "classification": "bug"})
            self.assertTrue(result["admitted"])
            written = update.call_args.args[3][0]["field_value"]
            self.assertIn("原有人工正文", written)
            self.assertIn(self.event["github_url"], written)

    def test_placeholder_cannot_pass_completed_translation_check(self):
        source, target = self.target_fixture()
        target["work_item_attribute"]["work_item_name"] = "[待人工分诊] English report"
        result = {"decision": "classified", "classification": "bug", "target_id": "456",
                  "owner_userkeys": ["owner"], "follower_userkeys": ["follower"]}
        config = {"pm_follower_userkey": "follower", "simple_name": "demo"}
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "Placeholder"):
                verify_target(config, "creator", self.event, result)

    def test_manual_seed_preserves_existing_people_and_retries_api_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.admission_config(directory)
            admitted = {"classification": "bug", "target_id": "456"}
            _, target = self.target_fixture()
            target["work_item_fields"][0]["value"] += "\n待人工确认：边界不明"
            with patch("runner.get_item", return_value=target), \
                    patch("runner.verify_pm", return_value="321"), \
                    patch("runner.update_item") as update, patch("runner.link_placeholder"), \
                    patch("runner.verify_target", side_effect=lambda c, cr, e, r, **kw: r):
                result = seed_result(config, "creator", self.event, admitted, "待确认")
            self.assertEqual(result["owner_userkeys"], ["owner"])
            self.assertEqual(result["follower_userkeys"], ["321", "follower"])
            self.assertEqual(json.loads(update.call_args.args[3][0]["field_value"]), ["321", "follower"])
            with patch("runner.get_item", return_value=target), \
                    patch("runner.verify_pm", side_effect=RuntimeError("PM offline")):
                with self.assertRaisesRegex(RuntimeError, "PM offline"):
                    seed_result(config, "creator", self.event, admitted, "待确认")

    def test_source_link_errors_propagate_and_correct_status_key_is_written(self):
        config = {"project_key": "project", "review_source_status": "review"}
        source, _ = self.target_fixture()
        with patch("runner.get_item", return_value=source), \
                patch("runner.update_item") as update:
            link_placeholder(config, "creator", self.event, "456")
        self.assertEqual(update.call_args.args[3], [
            {"field_key": "field_84351b", "field_value": "review"}])
        with patch("runner.get_item", return_value=source), \
                patch("runner.update_item", side_effect=RuntimeError("source offline")):
            with self.assertRaisesRegex(RuntimeError, "source offline"):
                link_placeholder(config, "creator", self.event, "456")

    def test_pool_link_or_unverified_owner_never_passes(self):
        config = {"simple_name": "demo", "feature_type": "feature",
                  "pm_follower_userkey": "follower"}
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
             "value": "1sl5_gbh2" if kind == "bug" else "_amm6f04g"},
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
        config = {"simple_name": "demo", "feature_type": "feature",
                  "pm_follower_userkey": "follower"}
        result = {"decision": "classified", "classification": "bug", "target_id": "456",
                  "owner_userkeys": ["owner"], "follower_userkeys": ["follower"]}
        with patch("runner.get_item", side_effect=self.target_fixture()):
            self.assertTrue(verify_target(config, "creator", self.event, dict(result))["verified"])
        with patch("runner.get_item", side_effect=self.target_fixture()):
            with self.assertRaisesRegex(ValueError, "owner readback"):
                verify_target(config, "creator", self.event, dict(result, owner_userkeys=["wrong"]))
        source, target = self.target_fixture()
        target["work_item_fields"][2]["value"] = [{"key": "source-creator"}]
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "configured PM"):
                verify_target(config, "creator", self.event,
                              dict(result, follower_userkeys=["source-creator"]))
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

    def test_manual_review_requires_final_target_translation_pm_and_relation(self):
        config = {"simple_name": "demo", "feature_type": "feature",
                  "pm_follower_userkey": "follower", "review_source_status": "review"}
        result = {"decision": "manual_review", "classification": "bug", "target_id": "456",
                  "owner_userkeys": [], "follower_userkeys": ["follower"],
                  "reason": "负责人待确认"}
        source, target = self.target_fixture()
        source["work_item_fields"][3]["value"] = "review"
        target["work_item_fields"][1]["value"] = []
        target["work_item_fields"][0]["value"] += "\n待人工确认：职责规则未覆盖，需确认负责人"
        with patch("runner.get_item", side_effect=[source, target]):
            value = verify_target(config, "creator", self.event, dict(result))
            self.assertTrue(value["verified"])
            self.assertEqual(value["target_url"], "https://project.larksuite.com/demo/issue/detail/456")
        target["work_item_fields"][0]["value"] = (
            self.event["github_url"] + "\n待确认原因：中文职责边界需由PM确认")
        with patch("runner.get_item", side_effect=[source, target]):
            self.assertTrue(verify_target(config, "creator", self.event, dict(result))["verified"])
        for change in ({"target_id": "123"}, {"target_id": ""}):
            with self.assertRaisesRegex(ValueError, "final target"):
                verify_target(config, "creator", self.event, dict(result, **change))
        source["work_item_fields"][3]["value"] = "bgk7h9nl6"
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "owner readback"):
                verify_target(config, "creator", self.event, dict(result, decision="classified"))
        source["work_item_fields"][3]["value"] = "review"
        target["work_item_fields"][0]["value"] = self.event["github_url"]
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "reason absent"):
                verify_target(config, "creator", self.event, dict(result))
        target["work_item_fields"][0]["value"] += "\n待人工确认：  \n"
        with patch("runner.get_item", side_effect=[source, target]):
            with self.assertRaisesRegex(ValueError, "reason absent"):
                verify_target(config, "creator", self.event, dict(result))

    def test_manual_review_notification_has_formal_link_and_real_mention(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, output_dir=directory, job_id="job")
            result = {"decision": "manual_review", "title": "中文待分诊标题",
                      "target_url": "https://project.larksuite.com/demo/issue/detail/456",
                      "reason": "负责人待确认"}
            expected = (result["title"] + "\nGitHub: " + self.event["github_url"]
                        + "\nMeegle: " + result["target_url"]
                        + '\n<at user_id="ou_pm">77</at> 负责人待确认')
            raw = {"data": {"items": [{"message_id": "sent", "chat_id": "chat",
                    "mentions": [{"id": "ou_pm", "key": "@_user_1"}],
                    "body": {"content": json.dumps({"text": expected.replace(
                        '<at user_id="ou_pm">77</at>', "@_user_1")})}}]}}
            with patch.dict("sys.modules", {"cron.jobs": SimpleNamespace(
                    get_job=lambda _: {"personal_creator": "creator"})}), \
                    patch("runner.verify_target", return_value=result) as verify, \
                    patch("runner.cli", side_effect=[{"data": {"message_id": "sent"}},
                        {"data": {"messages": [{"message_id": "sent"}]}}, raw]) as command:
                notify(config, {"event": self.event, "result": result,
                                "chat_id": "chat", "uuid": "manual"})
            verify.assert_called_once()
            self.assertEqual(command.call_args_list[0].args[0][
                command.call_args_list[0].args[0].index("--text") + 1], expected)

    def test_notification_retry_reads_saved_receipt_without_resending(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, review_chat="review", output_dir=directory)
            data = {"event": self.event, "result": {"decision": "failed", "reason": "check"},
                    "chat_id": "chat", "uuid": "stable"}
            content = '自动处理未完成 #12\nGitHub: ' + self.event["github_url"] + '\n<at user_id="ou_pm">77</at> check'
            response = {"data": {"messages": [{"message_id": "sent", "content": content}]}}
            with patch("runner.cli", side_effect=[
                    {"data": {"message_id": "sent"}}, RuntimeError("readback offline")]):
                with self.assertRaises(RuntimeError):
                    notify(config, data)
            raw = {"data": {"items": [{"message_id": "sent", "chat_id": "chat",
                "mentions": [{"id": "ou_pm", "key": "@_user_1"}],
                "body": {"content": json.dumps({"text": content.replace(
                    '<at user_id="ou_pm">77</at>', "@_user_1")})}}]}}
            with patch("runner.cli", side_effect=[response, raw]) as command:
                self.assertEqual(notify(config, data), {"message_id": "sent"})
                self.assertEqual(command.call_count, 2)
            data["result"]["decision"] = "classified"
            with patch.dict("sys.modules", {"cron.jobs": SimpleNamespace(
                    get_job=lambda _: {"personal_creator": "creator"})}), \
                    patch("runner.verify_target", return_value=dict(
                        data["result"], title="重新翻译的标题", target_url="")), \
                    patch("runner.cli", side_effect=[response, raw]) as command:
                self.assertEqual(notify(dict(config, job_id="job"), data), {"message_id": "sent"})
                self.assertEqual(command.call_count, 2)
                self.assertIn("+messages-mget", command.call_args_list[0].args[0])
            data["result"]["reason"] = "different retry explanation"
            data["result"]["decision"] = "failed"
            with patch("runner.cli", side_effect=[response, raw]) as command:
                self.assertEqual(notify(config, data), {"message_id": "sent"})
                self.assertEqual(command.call_count, 2)

    def test_admission_checkpoint_precedes_model_and_delivery_retry_reuses_result(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(dict(self.config, state_dir=directory, runner_command=[]))
            self.addCleanup(service.lock.close)
            service.queue.enqueue("one", self.event)
            admitted = {"phase": "admitted", "classification": "bug", "target_id": "456", "admitted": True}
            with patch.object(service, "command", return_value=admitted) as call:
                service.admit(service.queue.claim("admission"))
                self.assertEqual(call.call_args.args[0], "ensure")
            self.assertEqual(service.queue.get("one")["result"]["target_id"], "456")
            target = dict(admitted, phase="triaged", decision="classified")
            with patch.object(service, "command", side_effect=[target, RuntimeError("offline")]) as call:
                with self.assertRaises(RuntimeError):
                    service.process(service.queue.claim("triage"))
                self.assertEqual(call.call_count, 2)
            service.queue.fail("one", "offline")
            service.queue.retry("one")
            with patch.object(service, "command", return_value={"message_id": "receipt"}) as call:
                service.process(service.queue.claim("triage"))
                self.assertEqual(call.call_args.args[0], "notify")
                self.assertEqual(call.call_count, 1)
            self.assertEqual(service.queue.get("one")["result"]["message_id"], "receipt")

    def test_slow_triage_does_not_block_other_event_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(dict(self.config, state_dir=directory))
            self.addCleanup(service.lock.close)
            service.queue.enqueue("slow", self.event)
            service.queue.admitted("slow", {"phase": "admitted", "target_id": "456"})
            model_started, release_model, new_admitted = (
                threading.Event(), threading.Event(), threading.Event())
            def command(mode, payload):
                if mode == "triage":
                    model_started.set()
                    release_model.wait(5)
                    raise RuntimeError("model deliberately blocked")
                self.assertEqual(mode, "ensure")
                service.stop.set()
                new_admitted.set()
                return {"phase": "admitted", "target_id": "789", "admitted": True}
            with patch.object(service, "command", side_effect=command):
                workers = [threading.Thread(target=service.worker, args=(stage,))
                           for stage in ("triage", "admission")]
                try:
                    workers[0].start()
                    self.assertTrue(model_started.wait(2))
                    service.queue.enqueue("new", dict(self.event, number=13))
                    workers[1].start()
                    self.assertTrue(new_admitted.wait(2))
                    workers[1].join(2)
                    self.assertEqual(service.queue.get("new")["result"]["target_id"], "789")
                    self.assertEqual(service.queue.get("slow")["status"], "processing")
                finally:
                    service.stop.set()
                    service.wake.set()
                    release_model.set()
                    for worker in workers:
                        if worker.ident:
                            worker.join(5)
                            self.assertFalse(worker.is_alive())

    def test_failure_notification_recovers_id_from_durable_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, output_dir=directory, job_id="job")
            save_json(event_path(config, self.event, "target-"),
                      {"target_id": "456", "classification": "bug"})
            target_url = "https://project.larksuite.com/demo/issue/detail/456"
            expected = ("自动处理未完成 #12\nGitHub: " + self.event["github_url"]
                        + "\nMeegle: " + target_url + '\n<at user_id="ou_pm">77</at> check')
            raw = {"data": {"items": [{"message_id": "sent", "chat_id": "chat",
                "mentions": [{"id": "ou_pm", "key": "@_user_1"}],
                "body": {"content": json.dumps({"text": expected.replace(
                    '<at user_id="ou_pm">77</at>', "@_user_1")})}}]}}
            with patch.dict("sys.modules", {"cron.jobs": SimpleNamespace(
                    get_job=lambda _: {"personal_creator": "creator"})}), \
                    patch("runner.verify_admission", return_value={"target_url": target_url}) as verify, \
                    patch("runner.cli", side_effect=[{"data": {"message_id": "sent"}},
                        {"data": {"messages": [{"message_id": "sent"}]}}, raw]):
                self.assertEqual(notify(config, {"event": self.event,
                    "result": {"decision": "failed", "reason": "check"},
                    "chat_id": "chat", "uuid": "journal"}), {"message_id": "sent"})
            self.assertEqual(verify.call_args.args[3]["target_id"], "456")

    def test_every_decision_uses_one_notification_chat(self):
        for decision in ("classified", "manual_review"):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as directory:
                service = Service(dict(self.config, state_dir=directory))
                self.addCleanup(service.lock.close)
                service.queue.enqueue("one", self.event)
                service.queue.admitted("one", {"phase": "triaged", "decision": decision, "target_id": "456"})
                with patch.object(service, "command", return_value={"message_id": "sent"}) as command:
                    service.process(service.queue.claim("triage"))
                self.assertEqual(command.call_args.args[1]["chat_id"], "chat")
                self.assertEqual(command.call_args.args[1]["uuid"],
                                 hashlib.sha256(b"one:target:456").hexdigest()[:32])

    def test_failure_alert_uses_same_chat_and_retains_known_target(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(dict(self.config, state_dir=directory))
            self.addCleanup(service.lock.close)
            row = {"key": "one", "payload": self.event, "attempts": 3}
            def alert(mode, data):
                service.stop.set()
                self.assertEqual(data["chat_id"], "chat")
                self.assertEqual(data["result"]["decision"], "failed")
                self.assertEqual(data["result"]["target_id"], "456")
                return {"message_id": "sent"}
            with patch.object(service.queue, "claim", return_value=row), \
                    patch.object(service.queue, "fail"), \
                    patch.object(service.queue, "get", return_value={"result": {"target_id": "456"}}), \
                    patch.object(service, "process", side_effect=RuntimeError("offline")), \
                    patch.object(service, "command", side_effect=alert):
                service.worker("triage")

    def test_other_destination_rejected_before_send(self):
        with patch("runner.cli") as command:
            for chat in ("mybots", "other", ""):
                with self.assertRaisesRegex(ValueError, "Destination"):
                    notify(self.config, {"event": self.event,
                        "result": {"decision": "manual_review"}, "chat_id": chat})
            command.assert_not_called()

    def test_plain_name_or_wrong_chat_is_not_verified_as_mention(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, output_dir=directory)
            data = {"event": self.event, "result": {"decision": "failed", "reason": "check"},
                    "chat_id": "chat", "uuid": "stable"}
            rendered = {"data": {"messages": [{"message_id": "sent", "content": "@77"}]}}
            for chat, mentions in (("chat", []), ("wrong", [{"id": "ou_pm"}]),
                                   ("chat", [{"id": "ou_someoneelse"}])):
                raw = {"data": {"items": [{"message_id": "sent", "chat_id": chat,
                                           "mentions": mentions,
                                           "body": {"content": '{"text":"@77"}'}}]}}
                responses = ([{"data": {"message_id": "sent"}}]
                             if not (Path(directory) / "notification-stable.json").exists()
                             else [])
                with self.subTest(chat=chat, mentions=mentions), \
                        patch("runner.cli", side_effect=responses + [rendered, raw]):
                    with self.assertRaisesRegex(ValueError, "mention/chat"):
                        notify(config, data)

    def test_pm_exact_account_and_inactive_or_ambiguous_rejected(self):
        config = {"project_key": "demo", "pm_follower_userkey": "12345",
                  "pm_email": "pm@example.test"}
        user = {"user_key": "12345", "status": "activated", "email": "pm@example.test"}
        with patch("runner.cli", return_value=[user]) as command:
            self.assertEqual(verify_pm(config, "creator"), "12345")
            args = command.call_args.args[0]
            self.assertEqual(json.loads(args[args.index("--params") + 1])["user_keys"],
                             ["12345"])
        for users in ([], [user, user], [dict(user, status="deactivated")],
                      [dict(user, user_key="54321")], [dict(user, email="wrong@test")]):
            with self.subTest(users=users), patch("runner.cli", return_value=users):
                with self.assertRaisesRegex(ValueError, "exact identity"):
                    verify_pm(config, "creator")

    def test_cli_accepts_meegle_user_list(self):
        from subprocess import CompletedProcess
        with patch("runner.subprocess.run",
                   return_value=CompletedProcess([], 0, '[{"user_key":"12345"}]', "")):
            self.assertEqual(cli(["meegle"]), [{"user_key": "12345"}])

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
