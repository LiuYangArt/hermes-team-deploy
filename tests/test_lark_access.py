"""Entry switch, three permission layers, and seven-day file cleanup. No live Lark connection."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "extensions" / "lark-access" / "policy.py"
ENROLL_PATH = ROOT / "extensions" / "lark-access" / "enroll.py"
CORE = ROOT.parent / "hermes-team"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load(POLICY_PATH, "lark_access_policy")


class ConfigTest(unittest.TestCase):
    def test_missing_file_enrolls_and_has_no_admins(self):
        config = policy.load_config(Path("/no/such/lark-access.json"))
        self.assertTrue(config.auto_enroll)
        self.assertEqual(config.admins, ())
        self.assertTrue(policy.should_enroll(config, platform="feishu", user_id="ou_new", is_bot=False))
        self.assertFalse(policy.should_enroll(config, platform="feishu", user_id="ou_new", is_bot=True))

    def test_switch_off_keeps_the_official_pairing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"auto_enroll": False, "admins": ["ou_a", "ou_b"]}), encoding="utf-8")
            config = policy.load_config(path)
        self.assertFalse(config.auto_enroll)
        self.assertFalse(policy.should_enroll(config, platform="feishu", user_id="ou_new", is_bot=False))
        self.assertTrue(policy.is_admin(config, "ou_b"))
        self.assertFalse(policy.is_admin(config, "ou_other"))

    def test_gateway_id_is_recognized_after_it_arrives_with_the_admin_id(self):
        config = policy.AccessConfig(admins=("ou_admin",))
        aliases = policy.observe_aliases(config, (), ("tenant-admin", "ou_admin"))
        self.assertTrue(policy.is_admin(config, "tenant-admin", aliases=aliases))
        untouched = policy.observe_aliases(config, aliases, ("tenant-other", "ou_other"))
        self.assertEqual(untouched, aliases)
        self.assertFalse(policy.is_admin(config, "tenant-other", aliases=untouched))


class PermissionTest(unittest.TestCase):
    def setUp(self):
        self.config = policy.AccessConfig(auto_enroll=True, admins=("ou_admin", "ou_second"))
        self.store = Path("/data/team-files")

    def _tool(self, tool, args, user="ou_other", background=False):
        return policy.tool_decision(
            platform="feishu", user_ids=(user,), tool_name=tool, args=args,
            config=self.config, store=self.store, background_review=background,
        )

    def test_non_admin_shared_changes_are_refused_without_approval(self):
        for tool, args in (
            ("skill_manage", {"action": "create", "name": "demo"}),
            ("memory", {"action": "add", "content": "note"}),
            ("write_file", {"path": "/opt/data/SOUL.md", "content": "x"}),
            ("patch", {"path": "/opt/data/skills/demo/SKILL.md"}),
            ("terminal", {"command": "rm /opt/data/memories/MEMORY.md"}),
        ):
            decision = self._tool(tool, args)
            self.assertEqual(decision["action"], "block", tool)
            self.assertNotIn("approve", decision)
            self.assertIn("管理员", decision["message"])

    def test_admin_can_change_shared_settings(self):
        self.assertIsNone(self._tool("skill_manage", {"action": "create"}, user="ou_admin"))
        self.assertIsNone(self._tool("memory", {"action": "replace"}, user="ou_second"))
        self.assertIsNone(self._tool("write_file", {"path": "/opt/data/SOUL.md"}, user="ou_admin"))

    def test_nobody_can_change_the_program_list_switch_secrets_or_plugins(self):
        locked = (
            ("write_file", {"path": "/opt/hermes/gateway/run.py"}),
            ("write_file", {"path": "/opt/data/lark-access/config.json"}),
            ("patch", {"path": "/opt/data/.env"}),
            ("write_file", {"path": "/opt/data/config.yaml"}),
            ("terminal", {"command": "hermes plugins install demo"}),
            ("write_file", {"path": "/opt/data/plugins/demo/plugin.yaml"}),
        )
        for user in ("ou_other", "ou_admin"):
            for tool, args in locked:
                decision = self._tool(tool, args, user=user)
                self.assertEqual(decision["action"], "block", (user, tool, args))
                self.assertIn("不能在对话里改", decision["message"])

    def test_ordinary_files_move_into_independent_storage(self):
        decision = self._tool("write_file", {"path": "/workspace/草稿/清单.md", "content": "1"})
        self.assertEqual(decision["action"], "modify")
        self.assertEqual(decision["args"]["path"], "/data/team-files/草稿/清单.md")
        self.assertIsNone(self._tool("write_file", {"path": "/data/team-files/已在库.md"}))

    def test_automatic_skill_and_memory_writes_are_refused(self):
        decision = self._tool("skill_manage", {"action": "create"}, user="ou_admin", background=True)
        self.assertEqual(decision["action"], "block")
        self.assertIn("不会自动", decision["message"])
        memory = self._tool("memory", {"action": "add"}, user="ou_admin", background=True)
        self.assertEqual(memory["action"], "block")

    def test_cli_turns_are_left_alone(self):
        decision = policy.tool_decision(
            platform="", user_ids=("",), tool_name="skill_manage", args={"action": "create"},
            config=self.config, store=self.store, background_review=False,
        )
        self.assertIsNone(decision)

    def test_model_switch_persists_for_admins_and_is_refused_for_others(self):
        self.assertEqual(
            policy.inbound_text("/model deepseek-chat", is_admin_sender=True),
            "/model deepseek-chat --global",
        )
        self.assertEqual(policy.inbound_text("/model", is_admin_sender=False), "/model")
        refused = policy.inbound_text("背景\n\n/model other", is_admin_sender=False)
        self.assertIn("已经拒绝", refused)
        self.assertFalse(refused.startswith("/"))

    def test_plugin_install_command_is_refused_for_an_admin(self):
        refused = policy.inbound_text("/plugins install demo", is_admin_sender=True)
        self.assertIn("不能在对话里改", refused)


class CleanupTest(unittest.TestCase):
    def test_only_files_older_than_seven_days_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old" / "draft.md"
            fresh = root / "fresh" / "note.md"
            active = root / "active.md"
            old.parent.mkdir()
            fresh.parent.mkdir()
            old.write_text("old", encoding="utf-8")
            fresh.write_text("fresh", encoding="utf-8")
            active.write_text("active", encoding="utf-8")
            now = datetime(2026, 10, 1, 4, 22, tzinfo=policy.SHANGHAI)
            old_stamp = (now - timedelta(days=8)).timestamp()
            fresh_stamp = (now - timedelta(days=6)).timestamp()
            os.utime(old, (old_stamp, old_stamp))
            os.utime(fresh, (fresh_stamp, fresh_stamp))
            os.utime(active, (now.timestamp(), now.timestamp()))
            result = policy.cleanup_expired(root, now)
            self.assertEqual(result["removed"], ["old/draft.md"])
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(active.exists())
            self.assertFalse(old.parent.exists())
            self.assertTrue(fresh.parent.exists())

    def test_cleanup_runs_after_four_twenty_two_and_only_once_that_day(self):
        morning = datetime(2026, 10, 1, 4, 22, tzinfo=policy.SHANGHAI).astimezone(timezone.utc)
        early = datetime(2026, 10, 1, 4, 21, tzinfo=policy.SHANGHAI).astimezone(timezone.utc)
        self.assertFalse(policy.cleanup_due(early, None))
        self.assertTrue(policy.cleanup_due(morning, None))
        self.assertFalse(policy.cleanup_due(morning, morning.astimezone(policy.SHANGHAI).date()))


class RecognizedListTest(unittest.TestCase):
    def test_new_colleague_is_visible_to_the_official_list(self):
        if sys.version_info < (3, 10):
            self.skipTest("official pairing store imports on the gateway Python")
        sys.path.insert(0, str(CORE))
        enroll = _load(ENROLL_PATH, "lark_access_enroll")
        import gateway.pairing as pairing

        previous = pairing.PAIRING_DIR
        with tempfile.TemporaryDirectory() as tmp:
            pairing.PAIRING_DIR = Path(tmp)
            try:
                self.assertTrue(enroll.record_recognized("ou_new", "colleague"))
                self.assertFalse(enroll.record_recognized("ou_new", "colleague"))
                store = pairing.PairingStore()
                self.assertTrue(store.is_approved("feishu", "ou_new"))
                saved = json.loads((Path(tmp) / "feishu-approved.json").read_text(encoding="utf-8"))
                self.assertIn("ou_new", saved)
            finally:
                pairing.PAIRING_DIR = previous
