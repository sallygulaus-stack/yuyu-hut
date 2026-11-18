"""First-run assets, preferences and isolation of the optional host test window."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "panel"))
sys.path.insert(0, str(ROOT / "tools"))
import launch
from desktop_window import DESKTOP_UNAVAILABLE
from starter_assets import seed_food
from test_room import TestRoom


class FirstUse(unittest.TestCase):
    def test_personal_library_starts_empty_rice_is_not_restored_after_deletion(self):
        with tempfile.TemporaryDirectory() as location:
            home = Path(location)
            env = {**os.environ, "LOCALAPPDATA": location, "XDG_DATA_HOME": location, "HOME": location,
                   "PYTHONIOENCODING": "utf-8"}
            env.pop("QQ_PANEL_DATA_DIR", None)
            def boot():
                proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/launch.py"),
                                       "--use-defaults", "--smoke-test"], cwd=home, env=env, capture_output=True,
                                      encoding="utf-8", timeout=25, check=True)
                return Path(json.loads(proc.stdout.splitlines()[0])["data_dir"])
            data = boot()
            self.assertTrue(data.is_relative_to(home))
            self.assertFalse((data / ".demo-seeded").exists())
            food = data / "food/白米饭.png"
            self.assertEqual(hashlib.sha256(food.read_bytes()).digest(),
                             hashlib.sha256((ROOT / "panel/web/assets/starter-rice.png").read_bytes()).digest())
            food.unlink()
            self.assertEqual(boot(), data)
            self.assertFalse(food.exists())

    def test_existing_food_never_overwritten(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            (root / "food").mkdir()
            food = root / "food/白米饭.png"
            food.write_bytes(b"previous user asset")
            seed_food(root, ROOT / "panel/web")
            self.assertEqual(food.read_bytes(), b"previous user asset")

    @unittest.skipUnless(sys.platform == "win32", "Windows desktop launcher")
    def test_launcher_falls_back_to_browser_with_the_selected_library(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            selected = root / "selected"
            with (patch.object(launch, "ROOT", root / "source"),
                  patch.object(launch, "user_data_dir", return_value=root / "home"),
                  patch.dict(os.environ, {}, clear=True),
                  patch.object(sys, "argv", ["launch.py", "--desktop-first"]),
                  patch.object(launch, "choose", return_value=selected) as choose_dir,
                  patch.object(launch, "offer_browser", return_value=True) as offer,
                  patch.object(launch.subprocess, "call", side_effect=[DESKTOP_UNAVAILABLE, 0]) as call):
                self.assertEqual(launch.main(), 0)
            choose_dir.assert_called_once()
            offer.assert_called_once()
            commands = [attempt.args[0] for attempt in call.call_args_list]
            self.assertEqual(commands[0][-3:], ["--data-dir", str(selected), "--desktop"])
            self.assertEqual(commands[1][-2:], ["--data-dir", str(selected)])
            self.assertEqual(call.call_args.kwargs["creationflags"], subprocess.CREATE_NEW_CONSOLE)
            prefs = json.loads((root / "home/launcher.json").read_text(encoding="utf-8"))
            self.assertEqual(prefs["data_dir"], str(selected))

    def test_explicit_launch_commands_do_not_open_the_directory_prompt(self):
        for args in (["--no-browser"], ["--browser"], ["--smoke-test"], ["--desktop"], ["--help"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as location:
                root = Path(location)
                with (patch.object(launch, "ROOT", root / "source"),
                      patch.object(launch, "user_data_dir", return_value=root / "home"),
                      patch.dict(os.environ, {}, clear=True),
                      patch.object(sys, "argv", ["launch.py", "--desktop-first", *args]),
                      patch.object(launch, "choose") as choose_dir,
                      patch.object(launch, "offer_browser") as offer,
                      patch.object(launch.subprocess, "call", return_value=0) as call):
                    self.assertEqual(launch.main(), 0)
                choose_dir.assert_not_called()
                offer.assert_not_called()
                call.assert_called_once()
                self.assertEqual("--desktop" in call.call_args.args[0], args == ["--desktop"])

    def test_test_room_is_opt_in_and_refuses_another_recipient(self):
        with patch("test_room._get") as wire:
            self.assertFalse(TestRoom({}).call("send", ["sample"])["ok"])
            wire.assert_not_called()
        with tempfile.TemporaryDirectory() as loc:
            credential = Path(loc) / "test-auth"
            credential.write_text("temporary")
            room = TestRoom({"HUT_TEST_FEED":"http://127.0.0.1:1000", "HUT_TEST_BRIDGE":"http://127.0.0.1:1001",
                             "HUT_TEST_PEER":"300", "HUT_TEST_BRIDGE_TOKEN_FILE":str(credential)})
            state = {"ok":True, "peerId":301, "connections":1}
            bridge = {"mode":"closed-agent", "ownerQQ":300, "allowPrivate":[300], "allowGroups":[],
                      "dshReady":True, "snowluma":{"connected":True}}
            room.feed_request = Mock(return_value=state)
            with patch("test_room._get", return_value=bridge):
                for method, args in (("send", ["marker"]), ("history", [30, 0]), ("poll", [0, None])):
                    self.assertFalse(room.call(method, args)["ok"])
                self.assertTrue(all(call.args[0] == "/__state" for call in room.feed_request.call_args_list))
                state["peerId"] = 300
                bridge["allowGroups"] = [1]
                self.assertFalse(room.call("send", ["marker"])["ok"])
                bridge["allowGroups"] = []
                state["connections"] = 2
                self.assertFalse(room.call("send", ["marker"])["ok"])
                state["connections"] = 1
                room.feed_request.side_effect = [state, OSError("uncertain response")]
                self.assertIn("未确认", room.call("send", ["marker"])["error"])
                self.assertEqual(sum(call.args[0] == "/__inject" for call in room.feed_request.call_args_list), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
