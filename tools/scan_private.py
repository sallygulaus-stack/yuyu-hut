"""Redacted release scan of candidate files and every reachable Git revision."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

RULES = {
    "private-key": re.compile(r"-{5}BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-{5}"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "cloud-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "api-secret": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b"),
    "literal-secret": re.compile(
        r"""(?i)["']?(?:password|passwd|access_token|api_key|client_secret|oauth_token|token)["']?\s*[:=]\s*["'](?![<${])([A-Za-z0-9_!+./=-]{8,})["']"""),
    "windows-absolute-path": re.compile(r"(?<![A-Za-z])(?i:[C-Z]):[\\/](?![\\/])"),
    "personal-home": re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    "private-address": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"),
    "embedded-http-credential": re.compile(r"https?://[^\s/@\"']+:[^\s/@\"']+@"),
    "legacy-machine-password": re.compile(r"(?i)\bqqbot\d{4,}\b"),
}
FORBIDDEN_PATH = re.compile(
    r"(?i)(?:^|/)(?:AGENTS\.md|id_[^/]+|config\.json|[^/]+\.(?:jsonl|sqlite3?|db|pem|key|log|bak)|__pycache__)(?:/|$)")
MEDIA_PATH = re.compile(r"(?i)\.(?:png|ico|jpe?g|gif|webp|mp3|wav|m4a|mp4|zip|bundle)$")
RELEASE_ART = {
    "docs/images/gallery-demo.png": "001b55d7352373a234c6d79273bfcbe41fe69e6debc28204972ec565754c9f50",
    "panel/web/assets/app-icon.png": "79bb720455db6c08946047ff4834b9081273f8aa3329a11ec66b1247dfe0d25e",
    "panel/web/assets/app-icon.ico": "d8d22392b66e0d3d73b1e5246983203da670e6c1bd505b887670981550237aae",
    "panel/web/assets/default-me.png": "9f8719dc22b2e154267f14c7ca5e271554c054afbdc21d69b8386688c2f03b60",
    "panel/web/assets/default-peer.png": "8a8c4892eb2cb759c02642474d3f3c5a08285aa6012bf433255bbffc7001916b",
    "panel/web/assets/starter-rice.png": "e5fa656892d13c7a2e3b782159cea4612f6282a22b07d451946b0b931f70e7c5",
}


def git(root, *args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=root)


def scan(root, history=False):
    findings = []
    seen = set()
    checked = 0

    def inspect(name, data, revision):
        nonlocal checked
        checked += 1
        digest = hashlib.sha256(data).hexdigest()
        if FORBIDDEN_PATH.search(name) or (MEDIA_PATH.search(name) and RELEASE_ART.get(name) != digest):
            findings.append({"revision": revision, "path": name, "line": 0, "rule": "runtime-or-private-file"})
        signature = (name, digest)
        if signature in seen:
            return
        seen.add(signature)
        if b"\0" in data:
            return
        text = data.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for name_rule, pattern in RULES.items():
                if pattern.search(line):
                    findings.append({"revision": revision, "path": name,
                                     "line": line_number, "rule": name_rule})

    if history:
        revisions = git(root, "rev-list", "--all").decode().splitlines()
        for revision in revisions:
            for record in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
                if not record:
                    continue
                meta, name = record.split(b"\t", 1)
                _, kind, oid = meta.split()
                if kind == b"blob":
                    inspect(name.decode("utf-8"), git(root, "cat-file", "blob", oid.decode()), revision)
    else:
        revisions = ["worktree"]
        names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        for raw in sorted(set(names)):
            if raw:
                name = raw.decode("utf-8")
                path = root / name
                if path.is_file():
                    inspect(name, path.read_bytes(), "worktree")
    return {"scope": "history" if history else "worktree", "revisions": len(revisions),
            "files_checked": checked, "unique_versions": len(seen),
            "findings": findings, "secrets_redacted": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = scan(args.root.resolve(), args.history)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result["findings"]))
