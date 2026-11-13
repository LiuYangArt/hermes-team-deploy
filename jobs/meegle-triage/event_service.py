"""Authenticated ingestion and durable delivery; no GitHub/Meegle polling."""
import argparse
import fcntl
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from queue_store import QueueStore


LOG = logging.getLogger("meegle-triage")


def validate_event(value, config):
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    repo = value.get("repository")
    if repo not in config["routes"]:
        raise ValueError("Repository not allowed")
    number = value.get("number")
    source = str(value.get("source_id", ""))
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("Invalid issue number")
    if not re.fullmatch(r"[1-9][0-9]{0,18}", source):
        raise ValueError("Invalid source ID")
    expected = f"https://github.com/{repo}/issues/{number}"
    if value.get("github_url") != expected:
        raise ValueError("Issue URL mismatch")
    return {
        "repository": repo, "number": number, "source_id": source,
        "github_url": expected,
    }


class Service:
    def __init__(self, config):
        self.config = config
        state = Path(config["state_dir"])
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = (state / "service.lock").open("w")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.queue = QueueStore(state / "events.sqlite")
        self.queue.recover()
        self.wake = threading.Event()
        self.stop = threading.Event()

    def command(self, mode, payload):
        proc = subprocess.run(
            self.config["runner_command"] + [mode],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=1800 if mode == "triage" else 180)
        if proc.returncode:
            raise RuntimeError(f"Runner {mode} failed (exit {proc.returncode}): "
                               + proc.stderr[-1000:])
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"Runner {mode} returned invalid JSON") from None

    def process(self, row):
        payload = row["payload"]
        result = row["result"]
        # Persist the verified target before delivery, so retries cannot reclassify it.
        if not result:
            result = self.command("triage", payload)
            self.queue.checkpoint(row["key"], result)
        if result["decision"] == "filtered":
            self.queue.finish(row["key"], result)
            return
        target = self.config["routes"][payload["repository"]]
        if result["decision"] != "classified":
            target = self.config["review_chat"]
        receipt = self.command("notify", {
            "event": payload, "result": result, "chat_id": target,
            "uuid": hashlib.sha256(row["key"].encode()).hexdigest()[:32],
        })
        result["message_id"] = receipt["message_id"]
        self.queue.finish(row["key"], result)

    def worker(self):
        while not self.stop.is_set():
            self.queue.retry_transient()
            row = self.queue.claim()
            if row:
                try:
                    LOG.info("processing %s attempt=%s", row["key"], row["attempts"])
                    self.process(row)
                    LOG.info("completed %s", row["key"])
                except Exception as exc:
                    self.queue.fail(row["key"], str(exc))
                    LOG.exception("failed %s", row["key"])
                    if row["attempts"] >= 3:
                        try:
                            self.command("notify", {
                                "event": row["payload"],
                                "result": {"decision": "manual_review",
                                           "reason": "自动处理失败，事件已保留。请检查分诊服务的失败记录。"},
                                "chat_id": self.config["review_chat"],
                                "uuid": hashlib.sha256(
                                    (row["key"] + ":failed").encode()).hexdigest()[:32],
                            })
                        except Exception:
                            LOG.exception("failure alert could not be delivered")
                continue
            self.wake.wait(10)
            self.wake.clear()

    def handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                LOG.info("%s %s", self.command, self.path.split("?")[0])

            def respond(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                supplied = self.headers.get("Authorization", "")
                return hmac.compare_digest(supplied, "Bearer " + service.config["token"])

            def do_GET(self):
                if self.path == "/hooks/meegle-triage/health":
                    self.respond(200, {"ok": True})
                elif self.path.startswith("/hooks/meegle-triage/status/") and self.authorized():
                    key = self.path.rsplit("/", 1)[-1]
                    row = service.queue.get(key)
                    self.respond(200 if row else 404, row or {"error": "not_found"})
                else:
                    self.respond(404, {"error": "not_found"})

            def do_POST(self):
                self.connection.settimeout(15)
                if self.path != "/hooks/meegle-triage":
                    self.respond(404, {"error": "not_found"})
                    return
                if not self.authorized():
                    self.respond(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        raise ValueError("Invalid content length")
                    raw = self.rfile.read(length)
                    event = validate_event(json.loads(raw), service.config)
                    key = event["repository"].replace("/", ":") + ":" + str(event["number"])
                    added = service.queue.enqueue(key, event)
                    row = service.queue.get(key)
                    if row["payload"] != event:
                        self.respond(409, {"error": "Existing issue has a different source"})
                        return
                    service.wake.set()
                    self.respond(202, {"accepted": True, "key": key,
                                       "duplicate": not added, "status": row["status"]})
                except (ValueError, TypeError, KeyError):
                    self.respond(400, {"error": "invalid_event"})

        return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = Service(json.loads(Path(args.config).read_text()))
    thread = threading.Thread(target=service.worker, daemon=True)
    thread.start()
    server = ThreadingHTTPServer(
        (service.config["bind"], service.config["port"]), service.handler())
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        service.stop.set()
        service.wake.set()
        server.server_close()


if __name__ == "__main__":
    main()
