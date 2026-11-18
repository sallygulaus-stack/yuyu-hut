"""Start only synthetic services for the shipped browser regression."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "panel"))
sys.path.insert(0, str(PROJECT))
from tests.gateway_fixture import SyntheticStack

stack = SyntheticStack()
try:
    config = stack.root / "client.json"
    config.write_text(json.dumps(stack.panel_config()), encoding="utf-8")
    result = subprocess.run([shutil.which("node"), str(PROJECT / "tests/gateway-browser.cjs"), str(config)],
        env={**os.environ, "PYTHON": sys.executable}, cwd=PROJECT)
    raise SystemExit(result.returncode)
finally:
    stack.close()
