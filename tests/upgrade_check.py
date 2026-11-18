"""Read a new offline data directory from the previous and current source trees."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import build_opener, ProxyHandler, Request
import wave

from PIL import Image


PROJECT = Path(__file__).resolve().parents[1]
HTTP = build_opener(ProxyHandler({}))
DAY = "2026-01-02"
TEXT = "Synthetic rollback message."
DIARY = "Synthetic rollback diary.\nSecond line."
PHOTO = DAY + "-synthetic.png"


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def fixture_media():
    picture, recording = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (5, 7), (31, 139, 128)).save(picture, format="PNG")
    with wave.open(recording, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(1600))
    return {"image": picture.getvalue(), "record": recording.getvalue()}


def wait_for_start(process, log, data):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        output = log.read_text(encoding="utf-8", errors="replace")
        for line in output.splitlines():
            if not line.startswith('{"url":'):
                continue
            try:
                ready = json.loads(line)
            except json.JSONDecodeError:
                continue  # The process may still be writing this line.
            url = urlsplit(ready["url"])
            check(url.scheme == "http" and url.netloc == f"127.0.0.1:{url.port}" and
                  bool(url.port) and url.path == "/index.html" and not url.query and not url.fragment,
                  "Panel did not expose a loopback address")
            check(ready["mode"] == "demo" and Path(ready["data_dir"]).resolve() == data,
                  "Panel did not open the isolated demo directory")
            return f"http://{url.netloc}"
        if process.poll() is not None:
            break
        time.sleep(.05)
    raise RuntimeError("Panel failed to start:\n" + log.read_text(encoding="utf-8", errors="replace"))


@contextmanager
def panel(source, scratch, data, stage):
    log = scratch / (stage + ".log")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with log.open("wb") as output, subprocess.Popen(
        [sys.executable, "-B", str(source / "panel" / "panel.py"), "--demo",
         "--no-browser", "--port", "0", "--config", str(scratch / "config.json"),
         "--data-dir", str(data)],
        cwd=scratch, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
    ) as process:
        try:
            yield wait_for_start(process, log, data)
        finally:
            # A forced exit also exercises the application's committed writes.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def client(address):
    with HTTP.open(address + "/api/bootstrap", timeout=5) as response:
        token = json.load(response)["session_token"]

    def call(method, *args):
        request = Request(address + "/api/" + method, data=json.dumps(args).encode(),
                          headers={"Content-Type": "application/json", "X-Panel-Token": token})
        with HTTP.open(request, timeout=5) as response:
            result = json.load(response)
        check(result.get("ok"), f"{method} failed: {result}")
        return result
    return call


def snapshot(call, address, expected_media_ids):
    history = call("chat_history", 500)
    rows = history["消息"]
    check(len(rows) == history["总"], "The history window does not contain all records")
    messages = []
    media = {}
    for row in rows:
        message_id = row.get("message_id") or row["消息ID"]
        segments = [(part["type"], part.get("text"), part.get("媒体ID")) for part in row["段"]]
        messages.append((message_id, row["序"], row["谁"], row["conversation_id"], segments))
        if message_id in expected_media_ids:
            kind = expected_media_ids[message_id]
            attachment = next((part for part in row["段"] if part["type"] == kind), {})
            media_id = attachment.get("媒体ID")
            check(media_id and attachment.get("url") == "/media/" + quote(media_id, safe=""),
                  f"Missing {kind} attachment")
            with HTTP.open(address + attachment["url"], timeout=5) as response:
                content = response.read()
            media[kind] = (media_id, len(content), digest(content))
    identifiers = [row[0] for row in messages]
    check(len(set(identifiers)) == len(identifiers) and all(identifiers), "Invalid or duplicate message IDs")
    return messages, media


def diary_files(data):
    root = data / "diary"
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, digest(path.read_bytes()))
            for path in root.rglob("*") if path.is_file()}


def rehearsal(old_source):
    fixture = fixture_media()
    with tempfile.TemporaryDirectory(prefix="qq-panel-rollback-") as folder:
        scratch = Path(folder).resolve()
        data = scratch / "data"
        (scratch / "config.json").write_text("{}", encoding="ascii")
        with panel(PROJECT, scratch, data, "new-first") as address:
            call = client(address)
            start_count = call("chat_history", 1)["总"]
            text_id = call("chat_send", TEXT)["message_id"]
            verified = call("pond_status")
            archived = call("new_pond", verified["confirmation"])["归档"]
            media_ids = {
                call("chat_send_image", base64.b64encode(fixture["image"]).decode("ascii"),
                     "synthetic.png", "Synthetic photo.")["message_id"]: "image",
                call("chat_send_record", base64.b64encode(fixture["record"]).decode("ascii"),
                     "synthetic.wav", "Synthetic voice.")["message_id"]: "record",
            }
            revision = call("diary_save", DAY, DIARY, 0)["revision"]
            (data / "diary" / "photos" / PHOTO).write_bytes(fixture["image"])
            before = snapshot(call, address, media_ids)
            check(len(before[0]) == start_count + 6, "Expected three sends and three offline replies")
            check(any(row[0] == text_id and ("text", TEXT, None) in row[4] for row in before[0]),
                  "The synthetic text is missing from history")
            check({kind: values[2] for kind, values in before[1].items()} ==
                  {kind: digest(value) for kind, value in fixture.items()}, "New panel changed a media original")
            files = diary_files(data)
            check(set(files) == {DAY + ".md", "photos/" + PHOTO}, "Diary was not written as two ordinary files")
            check(files[DAY + ".md"][1] == digest(DIARY.encode("utf-8")), "Diary file body differs")

        with panel(old_source, scratch, data, "previous") as address:
            call = client(address)
            check(snapshot(call, address, media_ids) == before, "Previous panel changed chat or media")
            try:
                call("diary_read", DAY)
            except HTTPError as exc:
                with exc:
                    check(exc.code == 404, "Previous panel returned an unexpected diary status")
            else:
                raise AssertionError("Previous panel unexpectedly exposes the new diary")
        check(diary_files(data) == files, "Previous panel changed diary bytes, files or modification times")

        with panel(PROJECT, scratch, data, "new-again") as address:
            call = client(address)
            check(snapshot(call, address, media_ids) == before, "Reopened new panel changed chat or media")
            reopened = call("diary_read", DAY)
            check(reopened["body"] == DIARY and reopened["revision"] == revision and
                  [item["name"] for item in reopened["photos"]] == [PHOTO], "Reopened diary differs")
            shown = call("diary_image", DAY, PHOTO)["image"]
            check(base64.b64decode(shown.split(",", 1)[1], validate=True) == fixture["image"],
                  "Reopened diary photo differs")
        check(diary_files(data) == files, "Reopening changed the diary files")
    return {
        "result": "PASS", "read_order": ["new", "previous", "new"],
        "messages_each_stage": len(before[0]), "local_splits": int(bool(archived)),
        "media": {kind: {"bytes": values[1], "sha256": values[2]} for kind, values in before[1].items()},
        "diary_files": len(files), "diary_untouched_by_previous": True, "diary_reopened": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_source", type=Path, help="Path to the previous source tree")
    args = parser.parse_args()
    old_source = args.old_source.resolve()
    if old_source == PROJECT or not (old_source / "panel" / "panel.py").is_file():
        parser.error("Provide a separate previous source tree containing panel/panel.py")
    print(json.dumps(rehearsal(old_source), indent=2))


if __name__ == "__main__":
    main()
