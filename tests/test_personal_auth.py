"""Personal Lark task reads and Meegle stay with the person who asked. No live login."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "extensions" / "personal-auth" / "policy.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load(POLICY_PATH, "personal_auth_policy")


def _grant_lark(root: Path, speaker: str) -> None:
    path = policy.lark_config_dir(root, speaker) / "hermes" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "apps": [{"users": [{"userOpenId": speaker}]}],
    }), encoding="utf-8")


def _grant_meegle(root: Path, speaker: str) -> None:
    path = policy.meegle_home(root, speaker) / ".meegle" / "credentials.enc"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-real-token")


class IsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.links = {"tenant_a": "ou_a", "ou_a": "ou_a", "tenant_b": "ou_b", "ou_b": "ou_b"}
        _grant_lark(self.root, "ou_a")
        _grant_meegle(self.root, "ou_a")

    def tearDown(self):
        self.tmp.cleanup()

    def _tool(self, command, user="ou_a", alt="", tool="terminal"):
        return policy.tool_decision(
            user_ids=(user, alt),
            tool_name=tool,
            args={"command": command, "code": command, "path": command},
            root=self.root,
            links=self.links,
        )

    def test_only_a_uses_as_grant_for_own_tasks(self):
        decision = self._tool("lark-cli task +get-my-tasks --complete=false", user="tenant_a", alt="ou_a")
        command = decision["args"]["command"]
        self.assertIn(policy.lark_config_dir(self.root, "ou_a").as_posix(), command)
        self.assertNotIn("ou_b", command)
        self.assertNotIn("/.lark-cli", command)
        self.assertIn("+get-my-tasks", command)

    def test_b_cannot_use_as_task_grant(self):
        decision = self._tool("lark-cli task +get-related-tasks", user="ou_b")
        self.assertEqual(decision["action"], "block")
        self.assertIn("不会改用别人的登录", decision["message"])
        self.assertNotIn("ou_a", decision["message"])

    def test_only_a_uses_as_grant_for_meegle(self):
        decision = self._tool("/usr/local/bin/meegle workitem list --project demo", user="ou_a")
        command = decision["args"]["command"]
        self.assertIn(policy.meegle_home(self.root, "ou_a").as_posix(), command)
        self.assertNotIn("ou_b", command)
        self.assertNotIn("home/.meegle", command)
        self.assertNotIn("_retired-shared", command)

    def test_b_cannot_use_as_meegle_grant(self):
        decision = self._tool("meegle workitem create --name demo", user="ou_b")
        self.assertEqual(decision["action"], "block")
        self.assertIn("不会改用别人的登录", decision["message"])
        self.assertNotIn("ou_a", decision["message"])

    def test_missing_speaker_does_not_pick_a_grant(self):
        decision = self._tool("meegle workitem list", user="")
        self.assertEqual(decision["action"], "block")
        self.assertIn("看不出是谁", decision["message"])

    def test_task_changes_stay_on_the_bot_identity(self):
        self.assertIsNone(self._tool("lark-cli task +create --summary demo"))
        self.assertIsNone(self._tool("lark-cli task +comment --task-id t1 --content note"))
        mixed = self._tool("meegle workitem list && lark-cli task +complete --task-id t1")
        command = mixed["args"]["command"]
        self.assertIn(policy.meegle_home(self.root, "ou_a").as_posix(), command)
        self.assertIn(policy.bot_lark_dir(self.root).as_posix(), command)
        self.assertNotIn(policy.lark_config_dir(self.root, "ou_a").as_posix(), command)
        together = self._tool("lark-cli task +get-my-tasks && lark-cli task +create --summary demo")
        self.assertEqual(together["action"], "block")

    def test_own_tasks_cannot_be_forced_onto_the_bot(self):
        decision = self._tool("lark-cli task +get-my-tasks --as bot")
        self.assertEqual(decision["action"], "block")
        self.assertIn("机器人身份", decision["message"])

    def test_meegle_login_stays_in_the_speakers_directory(self):
        decision = self._tool("meegle auth login", user="ou_b")
        command = decision["args"]["command"]
        self.assertIn(policy.meegle_home(self.root, "ou_b").as_posix(), command)
        self.assertNotIn("ou_a", command)
        self.assertIn(policy.meegle_home(self.root, "ou_b").as_posix(), decision["ensure_dirs"][0])

    def test_bot_only_lark_config_is_not_a_personal_grant(self):
        path = policy.lark_config_dir(self.root, "ou_b") / "hermes" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"apps": [{"defaultAs": "bot", "users": None}]}), encoding="utf-8")
        decision = self._tool("lark-cli task +get-my-tasks", user="ou_b")
        self.assertEqual(decision["action"], "block")

    def test_foreign_paths_and_token_env_are_refused(self):
        for command in (
            "cat /opt/data/home/.meegle/credentials.enc",
            "cat /opt/data/personal-auth/ou_a/meegle-home/.meegle/credentials.enc",
            "MEEGLE_USER_ACCESS_TOKEN=abc meegle workitem list",
            "HOME=/opt/data/home meegle workitem list",
            "LARKSUITE_CLI_CONFIG_DIR=/opt/data/.lark-cli lark-cli task +get-my-tasks",
        ):
            decision = self._tool(command, user="ou_b")
            self.assertEqual(decision["action"], "block", command)
            self.assertNotIn("ou_a", decision["message"])

    def test_script_cannot_bypass_the_terminal_rule(self):
        decision = self._tool("import os\nos.system('meegle workitem list')", tool="execute_code")
        self.assertEqual(decision["action"], "block")
        decision = policy.tool_decision(
            user_ids=("ou_b",),
            tool_name="read_file",
            args={"path": "/opt/data/home/.meegle/credentials.enc"},
            root=self.root,
            links=self.links,
        )
        self.assertEqual(decision["action"], "block")

    def test_shared_meegle_login_is_retired_without_being_assigned(self):
        shared = self.root / "home" / ".meegle"
        marker = shared / "credentials.enc"
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b"shared-login")
        self.assertEqual(policy.retire_shared_meegle(self.root), "moved")
        self.assertFalse(shared.exists())
        retired = self.root / "personal-auth" / "_retired-shared" / "meegle" / "credentials.enc"
        self.assertTrue(retired.is_file())
        decision = self._tool("meegle workitem list", user="ou_b")
        self.assertEqual(decision["action"], "block")
        kept = self._tool("meegle workitem list", user="ou_a")
        self.assertNotIn("_retired-shared", kept["args"]["command"])
        self.assertFalse(policy.has_meegle_grant(self.root, "ou_b"))

    def test_ids_from_one_message_share_one_directory(self):
        links = policy.observe({}, ("tenant_a", "ou_a"))
        self.assertEqual(policy.canonical(("tenant_a",), links), "ou_a")
        other = policy.observe(links, ("ou_b",))
        self.assertEqual(policy.canonical(("ou_b",), other), "ou_b")
        self.assertNotEqual(policy.canonical(("tenant_a",), other), "ou_b")


if __name__ == "__main__":
    unittest.main()
