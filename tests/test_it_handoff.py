import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import healthcheck  # noqa: E402
import preflight  # noqa: E402
EXAMPLE = ROOT / "deploy" / "bot.env.example"
COMPOSE = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")


def write_ready(state: Path, secret: str = "real-secret-value") -> None:
    (state / "data" / "lark-access").mkdir(parents=True)
    (state / "workspace").mkdir()
    (state / "data" / "config.yaml").write_text("model:\n  provider: test\n", encoding="utf-8")
    bot = state / "bot.env"
    bot.write_text(
        "\n".join(
            [
                "FEISHU_APP_ID=cli_real",
                f"FEISHU_APP_SECRET={secret}",
                "FEISHU_DOMAIN=lark",
                "FEISHU_CONNECTION_MODE=websocket",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bot.chmod(stat.S_IRUSR | stat.S_IWUSR)
    (state / "data" / "lark-access" / "config.json").write_text(
        json.dumps({"auto_enroll": False, "admins": ["ou_real_admin"]}),
        encoding="utf-8",
    )


class BlankTemplateTest(unittest.TestCase):
    def test_example_has_empty_credential_fields(self):
        values = preflight.parse_env(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(values["FEISHU_APP_ID"], "")
        self.assertEqual(values["FEISHU_APP_SECRET"], "")
        self.assertEqual(values["FEISHU_DOMAIN"], "lark")
        self.assertEqual(values["FEISHU_CONNECTION_MODE"], "websocket")
        self.assertNotIn("cli_", EXAMPLE.read_text(encoding="utf-8"))


class PreflightTest(unittest.TestCase):
    def test_ready_install_passes_without_printing_the_secret(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            secret = "super-secret-value"
            write_ready(state, secret)
            problems, notes = preflight.run_checks(
                state, ROOT, COMPOSE, DOCKERFILE, lambda host, port: None
            )
            text = preflight.report(problems, notes)
            self.assertEqual(problems, [])
            self.assertNotIn(secret, text)
            self.assertIn("安装前检查通过", text)
            self.assertEqual(notes, [])

    def test_blank_copy_is_rejected_and_stays_outside_the_repo(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            write_ready(state)
            (state / "bot.env").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            (state / "data" / "lark-access" / "config.json").write_text(
                (ROOT / "extensions" / "lark-access" / "config.example.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            problems, _notes = preflight.run_checks(
                state, ROOT, COMPOSE, DOCKERFILE, lambda host, port: None
            )
            self.assertIn("机器人凭据还没填完整", problems)
            self.assertIn("管理员名单还是示例，请换成真人的标识", problems)

    def test_state_inside_the_repo_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            state = Path(raw)
            write_ready(state)
            problems, _notes = preflight.run_checks(
                state, ROOT, COMPOSE, DOCKERFILE, lambda host, port: None
            )
            self.assertIn("状态目录不能放在代码仓库里面", problems)

    def test_network_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            write_ready(state)

            def fail(host, port):
                raise OSError("offline")

            problems, _notes = preflight.run_checks(state, ROOT, COMPOSE, DOCKERFILE, fail)
            self.assertTrue(any("连不上" in item for item in problems))


class HealthcheckTest(unittest.TestCase):
    def test_redacts_secrets_and_detects_a_fault_after_connect(self):
        secret = "super-secret-value"
        raw = (
            "connected to wss://example/ws?access_key=abcdef123456&ticket=zzzzzzzz\n"
            f"FEISHU_APP_SECRET={secret}\n"
            "Traceback (most recent call last)\n"
        )
        hidden = healthcheck.redact(raw, (secret,))
        self.assertNotIn(secret, hidden)
        self.assertNotIn("abcdef123456", hidden)
        self.assertNotIn("zzzzzzzz", hidden)
        self.assertIn("[已隐藏]", hidden)
        ok, summary = healthcheck.assess(True, raw)
        self.assertFalse(ok)
        self.assertIn("不可用", summary)

    def test_connected_container_is_healthy(self):
        ok, summary = healthcheck.assess(True, "gateway up\nconnected to wss://example/ws\n")
        self.assertTrue(ok)
        self.assertIn("已经连上", summary)

    def test_a_later_disconnect_is_a_fault_and_a_reconnect_recovers(self):
        down, _summary = healthcheck.assess(
            True, "connected to wss://example/ws\ndisconnected to wss://example/ws\n"
        )
        self.assertFalse(down)
        up, summary = healthcheck.assess(
            True,
            "disconnected to wss://example/ws\nconnected to wss://example/ws\n",
        )
        self.assertTrue(up)
        self.assertIn("已经连上", summary)

    def test_stopped_container_is_a_fault(self):
        ok, summary = healthcheck.assess(False, "websocket connected\n")
        self.assertFalse(ok)
        self.assertIn("没有在运行", summary)


class SingleRepositoryInstallTest(unittest.TestCase):
    def test_build_downloads_the_pinned_public_program(self):
        script = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")
        example = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("../hermes-team", script)
        self.assertIn("https://github.com/NousResearch/hermes-agent.git", example)
        self.assertIn("HERMES_CORE_REVISION=f8489405600c9a7d9d2f307dace086f18d7173ba", example)
        self.assertIn("https://github.com/LiuYangArt/hermes-team-deploy", readme)
        self.assertNotIn("git clone https://github.com/LiuYangArt/hermes-team.git", readme)
        self.assertIn("./scripts/build.sh", readme)


class ImageBoundaryTest(unittest.TestCase):
    def test_build_context_keeps_jobs_and_secrets_out(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertTrue(ignore.startswith("*"))
        self.assertNotIn("!jobs", ignore)
        self.assertEqual(preflight.image_problems(DOCKERFILE), [])
        self.assertEqual(preflight.compose_problems(COMPOSE), [])


if __name__ == "__main__":
    unittest.main()
