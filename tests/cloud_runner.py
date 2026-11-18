"""Bounded synthetic test group; run only inside the verified constrained unit."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request


def memstats():
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        mem[key] = int(value.strip().split()[0]) * 1024
    vm = dict(line.split() for line in Path("/proc/vmstat").read_text().splitlines())
    return {"available": mem["MemAvailable"], "swap_used": mem["SwapTotal"] - mem["SwapFree"],
            "pswpin": int(vm["pswpin"]), "pswpout": int(vm["pswpout"]),
            "oom_kill": int(vm.get("oom_kill", 0)),
            "load": Path("/proc/loadavg").read_text().split()[:3]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != Path("/opt/onebot-cloudtest") or not root.is_dir():
        raise SystemExit("invalid isolated run root")
    code_root = root / "code" if (root / "code").is_dir() else root
    config_root = root / "config" if (root / "config").is_dir() else root
    evidence = root / "evidence" if (root / "evidence").is_dir() else root
    if args.config_dir:
        config_root = args.config_dir.resolve()
    if args.evidence_dir:
        evidence = args.evidence_dir.resolve()
    if not config_root.is_relative_to(root) or not evidence.is_relative_to(root):
        raise SystemExit("configuration/evidence must stay inside the isolated root")
    config = json.loads((config_root / "runner.json").read_text())
    cgroup_relative = next(line.split("::", 1)[1] for line in
                           Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
    cg = Path("/sys/fs/cgroup") / cgroup_relative.lstrip("/")
    required = {"memory.high": 128 * 1024 * 1024, "memory.max": 192 * 1024 * 1024,
                "memory.swap.max": 0, "pids.max": 64}
    observed = {name: int((cg / name).read_text()) for name in required}
    cpu = (cg / "cpu.max").read_text().split()
    if observed != required or cpu[0] == "max" or int(cpu[0]) / int(cpu[1]) > .20001 or os.geteuid() == 0:
        raise SystemExit("resource constraints were not applied")
    observed["cpu.max"] = " ".join(cpu)
    observed["uid"] = os.geteuid()
    (evidence / "limits.json").write_text(json.dumps(observed))
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    processes = {}
    commands = {
        "onebot": [config["node"], str(code_root / "server/onebot-local.mjs"),
                   "--config", str(config_root / "onebot.json"),
                   "--data-dir", str(Path(config.get("onebot_data_dir") or root / "data/onebot"))],
        "gateway": [sys.executable, "-B", str(code_root / "server/gateway.py"), "--config", str(config_root / "gateway.json")],
        "mock": [config["node"], str(code_root / "tests/mock-framework.mjs"), str(config_root / "mock.json")],
    }

    def consume(name, stream):
        target = logs / (name + ".log")
        for line in iter(stream.readline, b""):
            if target.exists() and target.stat().st_size + len(line) > 128 * 1024:
                # Both files belong solely to this new test directory.
                os.replace(target, logs / (name + ".previous"))
            with target.open("ab") as handle:
                handle.write(line[:8192])

    def start(name):
        existing = processes.get(name)
        if existing and existing.poll() is None:
            return
        proc = subprocess.Popen(commands[name], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        processes[name] = proc
        threading.Thread(target=consume, args=(name, proc.stdout), daemon=True).start()

    def stop(name):
        proc = processes.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    baseline = memstats()
    def business_state():
        result = {}
        for name in config.get("business_units", []):
            raw = subprocess.check_output(["systemctl", "show", name, "--property=ActiveState,SubState,NRestarts"],
                                          text=True)
            result[name] = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        return result
    baseline_business = business_state()
    low = swaps = high = 0
    reason = "normal stop"
    peak = 0
    min_available = baseline["available"]
    samples = []
    start_time = time.monotonic()
    previous = baseline
    command_file = evidence / "control.json"
    if command_file.exists():
        config["last_command"] = json.loads(command_file.read_text()).get("id")
    try:
        for name in commands:
            if name != "mock" or config.get("mock_enabled", True):
                start(name)
        while not stop_event.wait(1):
            state = memstats()
            current = int((cg / "memory.current").read_text())
            peak = max(peak, current)
            min_available = min(min_available, state["available"])
            samples.append({**state, "memory_current": current, "elapsed": round(time.monotonic() - start_time, 1)})
            samples = samples[-3600:]
            events = dict(line.split() for line in (cg / "memory.events").read_text().splitlines())
            current_limits = {name: int((cg / name).read_text()) for name in required}
            current_cpu = (cg / "cpu.max").read_text().split()
            if (current_limits != required or current_cpu != cpu):
                reason = "resource constraints changed"
                break
            if len(samples) % 5 == 0 and business_state() != baseline_business:
                reason = "business unit state changed"
                break
            low = low + 1 if state["available"] < 384 * 1024 * 1024 else 0
            swaps = swaps + 1 if state["pswpout"] > previous["pswpout"] else 0
            high = high + 1 if current > 160 * 1024 * 1024 else 0
            previous = state
            if low >= 10 or swaps >= 3 or high >= 5 or int(events.get("oom_kill", 0)) or state["oom_kill"] > baseline["oom_kill"]:
                reason = "safety threshold"
                break
            if time.monotonic() - start_time >= min(3500, int(config.get("runtime_seconds", 3500))):
                reason = "runtime limit"
                break
            if sum(p.stat().st_size for p in root.rglob("*")
                   if p.is_file() and p != Path(config["node"])) > min(
                    100, int(config.get("artifact_mib", 100))) * 1024 * 1024:
                reason = "artifact limit"
                break
            if command_file.exists():
                command = json.loads(command_file.read_text())
                # The controller only writes new monotonically increasing commands.
                if command.get("id") != config.get("last_command"):
                    config["last_command"] = command.get("id")
                    name = command.get("restart") or command.get("component")
                    if name in commands:
                        action = command.get("action", "restart")
                        if action in {"stop", "restart"}:
                            stop(name)
                        if action in {"start", "restart"}:
                            start(name)
                        if action == "stop":
                            processes.pop(name, None)
                    (evidence / "control-result.json").write_text(json.dumps({"id": command["id"], "ok": True}))
            for name, proc in processes.items():
                if proc.poll() is not None:
                    reason = "test child exited: " + name
                    stop_event.set()
                    break
            if len(samples) % 3 == 0:
                (evidence / "metrics.json").write_text(json.dumps({
                    "baseline": baseline, "current": state, "min_available": min_available,
                    "peak_memory": peak, "samples": samples, "limits": observed, "reason": "running"}))
    finally:
        for name in processes:
            stop(name)
        (evidence / "metrics.json").write_text(json.dumps({
            "baseline": baseline, "current": memstats(), "min_available": min_available,
            "peak_memory": peak, "samples": samples, "limits": observed, "reason": reason}))


if __name__ == "__main__":
    main()
