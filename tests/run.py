"""Run portable regression suites without opening any user data."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    commands = [[sys.executable, "-B", str(ROOT / file)] for file in (
        "tests/check_syntax.py", "tests/test_first_use.py", "tests/test_console.py", "tests/test_core.py",
        "tests/test_reliability.py", "tests/test_media_limits.py", "tests/test_app.py", "tests/test_adapters.py",
        "tests/archive/二轮回归.py",
        "tests/archive/验收.py", "tests/archive/集成验收.py",
        "tests/gallery/媒体库后端.py", "tests/gallery/画册后端.py",
    )]
    node = shutil.which("node")
    if not node:
        raise SystemExit("本地端回归需要 Node.js")
    commands.append([node, str(ROOT / "tests/onebot-roundtrip.cjs")])
    commands.append([node, str(ROOT / "tests/onebot-delivery.cjs")])
    if args.browser:
        commands.extend([
            [node, str(ROOT / "tests/test-room-browser.cjs")],
            [sys.executable, "-B", str(ROOT / "tests/test_console.py"), "--browser"],
            [sys.executable, "-B", str(ROOT / "tests/gateway-browser.py")],
            [node, str(ROOT / "tests/demo-browser.cjs")],
            [node, str(ROOT / "tests/mobile-browser.cjs")],
            [node, str(ROOT / "tests/gallery/画册浏览器.cjs")],
            [node, str(ROOT / "tests/gallery/画册浏览器.cjs"), "--媒体库"],
            [node, str(ROOT / "tests/gallery/画册浏览器.cjs"), "--排队"],
        ])
    failures = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON": sys.executable,
           "PYTHONIOENCODING": "utf-8"}
    for command in commands:
        print("RUN", Path(command[2] if command[0] == sys.executable else command[1]).name,
              *command[3:], flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode:
            failures.append(command)
    for command in failures:
        print("FAILED:", " ".join(command[1:]), flush=True)
    print("SUITES=%d PASSED=%d FAILED=%d" % (len(commands), len(commands) - len(failures), len(failures)), flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
