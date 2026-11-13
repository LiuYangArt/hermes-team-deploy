"""Publish only this task's code; runtime configuration and state stay untouched."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


FILES = (
    "event_service.py", "queue_store.py", "runner.py",
    "test_event_job.py", "publish.py", "test_publish.py",
    "event_instructions.md", "anycross_prepare.js", "anycross_receipt.js", "README.md",
)


def publish(source, root, job):
    if job != "meegle-triage":
        raise ValueError("This publisher requires --job meegle-triage")
    source, root = Path(source), Path(root)
    contents = {name: (source / name).read_bytes() for name in FILES}
    digest = hashlib.sha256()
    for name, data in sorted(contents.items()):
        digest.update(name.encode() + b"\0" + data)
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
    final = releases / ("event-" + digest.hexdigest()[:16])
    try:
        for name, data in contents.items():
            (stage / name).write_bytes(data)
        subprocess.run(
            [sys.executable, "-m", "unittest", "test_event_job", "-v"],
            cwd=stage, check=True, timeout=120)
        if final.exists():
            if any((final / name).read_bytes() != data for name, data in contents.items()):
                raise ValueError("Existing release contents changed")
            shutil.rmtree(stage)
        else:
            stage.rename(final)
        temporary = root / f".current-{os.getpid()}"
        try:
            temporary.symlink_to(final.relative_to(root), target_is_directory=True)
            temporary.replace(root / "current")
        finally:
            temporary.unlink(missing_ok=True)
        return final
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, choices=["meegle-triage"])
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    print(publish(args.source, args.root, args.job))
