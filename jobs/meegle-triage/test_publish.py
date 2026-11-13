import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from publish import FILES, publish


class PublishTests(unittest.TestCase):
    def test_atomic_task_only_and_failed_validation_preserves_current(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source = base / "source"
            source.mkdir()
            for name in FILES:
                (source / name).write_text("# sample\n")
            root = base / "jobs" / "meegle-triage"
            root.mkdir(parents=True)
            (root / "state").mkdir()
            (root / "state" / "events.sqlite").write_bytes(b"persisted-state")
            (root / "service-config.json").write_text("protected-config")
            other = base / "jobs" / "another-task"
            other.mkdir()
            (other / "runner.py").write_text("other-task")
            with patch("publish.subprocess.run") as validation:
                first = publish(source, root, "meegle-triage")
                self.assertEqual(validation.call_args.kwargs["cwd"].parent, root / "releases")
            self.assertEqual((root / "current").resolve(), first.resolve())
            (source / "runner.py").write_text("# next release\n")
            with patch("publish.subprocess.run",
                       side_effect=subprocess.CalledProcessError(1, "tests")):
                with self.assertRaises(subprocess.CalledProcessError):
                    publish(source, root, "meegle-triage")
            self.assertEqual((root / "current").resolve(), first.resolve())
            self.assertEqual((root / "current" / "runner.py").read_text(), "# sample\n")
            with patch("publish.subprocess.run"):
                second = publish(source, root, "meegle-triage")
            self.assertNotEqual(first, second)
            self.assertEqual((root / "current").resolve(), second.resolve())
            self.assertEqual((root / "state" / "events.sqlite").read_bytes(), b"persisted-state")
            self.assertEqual((root / "service-config.json").read_text(), "protected-config")
            self.assertEqual((other / "runner.py").read_text(), "other-task")
            self.assertFalse(list((root / "releases").glob(".staging-*")))
            with self.assertRaises(ValueError):
                publish(source, root, "another-task")


if __name__ == "__main__":
    unittest.main()
