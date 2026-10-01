"""A new Docker home receives Lark, Meegle, and the team extensions."""

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENABLE_PATH = ROOT / "deploy" / "enable_team_plugins.py"
STARTUP = ROOT / "deploy" / "team-startup.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


enable = _load(ENABLE_PATH, "enable_team_plugins")


class EnablePluginsTest(unittest.TestCase):
    def test_adds_the_list_under_an_existing_plugins_section(self):
        text = enable.ensure_team_plugins(
            "plugins:\n  # keep this\n  clone_timeout_seconds: 300\nmodel:\n  default: demo\n"
        )
        self.assertIn("  enabled:\n    - lark-topics\n    - lark-access\n    - personal-auth\n", text)
        self.assertIn("clone_timeout_seconds: 300", text)
        self.assertIn("model:\n  default: demo", text)

    def test_does_not_duplicate_plugins_that_are_already_enabled(self):
        text = enable.ensure_team_plugins(
            "plugins:\n  enabled:\n    - lark-topics\n    - other\n"
        )
        self.assertEqual(text.count("- lark-topics"), 1)
        self.assertIn("- other", text)
        self.assertIn("- personal-auth", text)

    def test_appends_a_plugins_section_when_the_config_has_none(self):
        text = enable.ensure_team_plugins("model:\n  default: demo\n")
        self.assertTrue(text.strip().endswith("- personal-auth"))
        self.assertIn("model:", text)


class FeishuCronToolsetTest(unittest.TestCase):
    def test_adds_cron_to_an_explicit_feishu_list_and_leaves_other_platforms(self):
        text = enable.ensure_feishu_cron_toolset(
            "platform_toolsets:\n  feishu:\n    - file\n    - memory\n  cli:\n    - file\nmodel:\n  default: demo\n"
        )
        self.assertIn("    - cronjob\n", text)
        self.assertEqual(text.count("- cronjob"), 1)
        self.assertIn("  cli:\n    - file\n", text)
        self.assertIn("default: demo", text)

    def test_does_not_invent_a_list_or_duplicate_cron(self):
        untouched = "model:\n  default: demo\n"
        self.assertEqual(enable.ensure_feishu_cron_toolset(untouched), untouched)
        present = "platform_toolsets:\n  telegram:\n    - file\n"
        self.assertEqual(enable.ensure_feishu_cron_toolset(present), present)
        already = enable.ensure_feishu_cron_toolset(
            "platform_toolsets:\n  feishu: [file, cronjob]\n"
        )
        self.assertEqual(already, "platform_toolsets:\n  feishu: [file, cronjob]\n")
        added = enable.ensure_feishu_cron_toolset("platform_toolsets:\n  feishu: [file, memory]\n")
        self.assertIn("feishu: [file, memory, cronjob]", added)


class StartupTest(unittest.TestCase):
    def test_startup_installs_tools_and_leaves_grants_and_jobs_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            extensions = root / "extensions"
            for name in ("lark-topics", "lark-access", "personal-auth"):
                plugin = extensions / name
                plugin.mkdir(parents=True)
                (plugin / "plugin.yaml").write_text(f"name: {name}\n", encoding="utf-8")
                (plugin / "marker.txt").write_text("from-image\n", encoding="utf-8")
            grant = home / "personal-auth" / "ou_a" / "meegle-home" / ".meegle"
            grant.mkdir(parents=True)
            (grant / "credentials.enc").write_bytes(b"keep")
            job = home / "jobs" / "demo"
            job.mkdir(parents=True)
            (job / "keep.txt").write_text("job\n", encoding="utf-8")
            other = home / "skills" / "other-skill"
            other.mkdir(parents=True)
            (other / "SKILL.md").write_text("stay\n", encoding="utf-8")
            (home / "plugins" / "personal-auth").mkdir(parents=True)
            (home / "plugins" / "personal-auth" / "marker.txt").write_text("old\n", encoding="utf-8")
            (home / "config.yaml").write_text("model:\n  default: demo\n", encoding="utf-8")
            stub = root / "activate.sh"
            stub.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    set -eu
                    test "$1" = "--activate"
                    mkdir -p "$HERMES_HOME/skills/lark-task" "$HERMES_HOME/skills/meegle"
                    echo lark > "$HERMES_HOME/skills/lark-task/SKILL.md"
                    echo meegle > "$HERMES_HOME/skills/meegle/SKILL.md"
                    """
                ),
                encoding="utf-8",
            )
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            env = os.environ.copy()
            env.update({
                "HERMES_HOME": str(home),
                "HERMES_TEAM_EXTENSIONS": str(extensions),
                "HERMES_OFFICIAL_TOOLS_SCRIPT": str(stub),
                "HERMES_ENABLE_PLUGINS": str(ENABLE_PATH),
            })
            subprocess.run(["sh", str(STARTUP)], check=True, env=env)
            self.assertEqual((home / "plugins" / "personal-auth" / "marker.txt").read_text(encoding="utf-8"), "from-image\n")
            self.assertEqual((grant / "credentials.enc").read_bytes(), b"keep")
            self.assertEqual((job / "keep.txt").read_text(encoding="utf-8"), "job\n")
            self.assertEqual((other / "SKILL.md").read_text(encoding="utf-8"), "stay\n")
            self.assertEqual((home / "skills" / "meegle" / "SKILL.md").read_text(encoding="utf-8"), "meegle\n")
            config = (home / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("- personal-auth", config)
            self.assertIn("default: demo", config)

    def test_image_build_copies_extensions_and_runs_startup(self):
        dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("team-extensions/personal-auth", dockerfile)
        self.assertIn("02-hermes-team", dockerfile)
        self.assertIn("install_official_tools.sh --image", dockerfile)
        self.assertIn("context: ..", compose)
        self.assertNotIn("jobs/", ignore.replace("!jobs", ""))
        self.assertIn("extensions/personal-auth/**", ignore)


if __name__ == "__main__":
    unittest.main()
