"""The real desktop gate must reject offscreen, greeter and remote substitutes."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "full_installer_desktop", Path(__file__).resolve().parents[1]
    / "tools/ci/full-installer-desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopAdmissionTests(unittest.TestCase):
    def test_only_real_active_local_wayland_user_session_is_accepted(self):
        valid = {"User": "1000", "Active": "yes", "Remote": "no", "Type": "wayland",
                 "Class": "user", "Seat": "seat0", "Service": "sddm-autologin"}
        self.assertTrue(desktop.is_desktop_session(valid))
        for key, value in (("User", "0"), ("Active", "no"), ("Remote", "yes"),
                           ("Type", "x11"), ("Type", "tty"), ("Class", "greeter"),
                           ("Seat", ""), ("Service", "login")):
            with self.subTest(key=key, value=value):
                self.assertFalse(desktop.is_desktop_session({**valid, key: value}))

    def test_desktop_environment_rejects_missing_display_and_wrong_editor(self):
        valid = {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland",
                 "XDG_CURRENT_DESKTOP": "KDE", "EDITOR": "nano", "VISUAL": "nano"}
        text = lambda values: "\n".join(f"{key}={value}" for key, value in values.items())
        self.assertEqual(desktop.imported_environment(text(valid)), valid)
        for key, value in (("WAYLAND_DISPLAY", ""), ("WAYLAND_DISPLAY", "../other"),
                           ("XDG_SESSION_TYPE", "offscreen"), ("EDITOR", "vim"),
                           ("VISUAL", ""), ("XDG_CURRENT_DESKTOP", "")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(RuntimeError):
                    desktop.imported_environment(text({**valid, key: value}))

    def test_qml_error_window_is_not_a_desktop_pass(self):
        errors = ("Error loading the QML file: file:///widget/main.qml: Type Foo unavailable",
                  'module "QtQuick.Controls" is not installed',
                  "file:///widget/main.qml:42: ReferenceError: controller is not defined",
                  "QQmlApplicationEngine failed to load component")
        self.assertEqual(desktop.qml_failures("\n".join(errors)), list(errors))
        self.assertEqual(desktop.qml_failures("Connected to real desktop bus\n"), [])


class NanoAcceptanceTests(unittest.TestCase):
    def test_preexisting_file_cannot_supply_a_false_editor_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.txt"
            path.write_bytes(b"expected\n")
            child = Mock()
            with patch.dict(sys.modules, {"pexpect": SimpleNamespace()}):
                with self.assertRaisesRegex(RuntimeError, "must not exist"):
                    desktop.drive_nano(child, path, "expected")
            child.send.assert_not_called()

    def test_missing_or_wrong_saved_bytes_are_rejected(self):
        for content in (None, b"expected", b"wrong\n"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "edit.txt"
                child = Mock()
                if content is not None:
                    child.sendcontrol.side_effect = lambda key: path.write_bytes(content)
                with patch.dict(sys.modules, {"pexpect": SimpleNamespace()}):
                    with self.assertRaisesRegex(TimeoutError, "exact expected bytes"):
                        desktop.drive_nano(child, path, "expected", timeout=0)

    def test_nonzero_editor_exit_rejects_even_correct_saved_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.txt"
            child = Mock(exitstatus=1)
            child.sendcontrol.side_effect = lambda key: path.write_bytes(b"expected\n")
            with patch.dict(sys.modules, {"pexpect": SimpleNamespace(EOF=EOFError)}):
                with self.assertRaisesRegex(RuntimeError, "exit successfully"):
                    desktop.drive_nano(child, path, "expected")


class CompanionContentTests(unittest.TestCase):
    def test_similar_window_from_another_process_cannot_supply_content(self):
        correct = Mock(name="BudsLink Companion")
        correct.get_process_id.return_value = 42
        unrelated = Mock(name="BudsLink Companion")
        unrelated.get_process_id.return_value = 24
        for apps, accepted in (([None, unrelated], False), ([correct, unrelated], True),
                               ([correct, correct], False)):
            registry = Mock(childCount=len(apps))
            registry.getChildAtIndex.side_effect = apps.__getitem__
            if accepted:
                self.assertIs(desktop.application_for_pid(registry, 42), correct)
            else:
                with self.assertRaisesRegex(RuntimeError, "owned by PID 42"):
                    desktop.application_for_pid(registry, 42)

    def setUp(self):
        self.labels = ["No compatible headphones connected", "Refresh status"]
        self.nodes = [
            {"path": "app.0", "role": "frame", "name": "Companion", "visible": True,
             "showing": True, "bounds": [0, 0, 500, 500]},
            {"path": "app.0.0", "role": "label", "name": self.labels[0],
             "visible": True, "showing": True, "bounds": [20, 20, 460, 40]},
            {"path": "app.0.1", "role": "push button", "name": self.labels[1],
             "visible": True, "showing": True, "bounds": [150, 300, 200, 40],
             "enabled": True, "actions": ["press"]},
        ]

    def test_blank_window_and_text_in_another_window_fail(self):
        self.assertEqual(set(desktop.validate_companion_nodes(self.nodes, self.labels)),
                         set(self.labels))
        with self.assertRaisesRegex(RuntimeError, "visible in-window"):
            desktop.validate_companion_nodes(self.nodes[:1], self.labels)
        self.nodes[1]["path"] = "another-app.0.0"
        with self.assertRaisesRegex(RuntimeError, "visible in-window"):
            desktop.validate_companion_nodes(self.nodes, self.labels)

    def test_hidden_zero_size_and_clipped_labels_fail(self):
        for key, value in (("visible", False), ("showing", False),
                           ("bounds", [20, 20, 0, 40]),
                           ("bounds", [20, 490, 460, 40]),
                           ("bounds", [-10, 20, 460, 40])):
            with self.subTest(key=key, value=value):
                nodes = [dict(node) for node in self.nodes]
                nodes[1][key] = value
                with self.assertRaisesRegex(RuntimeError, "visible in-window"):
                    desktop.validate_companion_nodes(nodes, self.labels)

    def test_labels_split_across_two_windows_in_same_process_fail(self):
        self.nodes.append({**self.nodes[0], "path": "app.1"})
        self.nodes[1]["path"] = "app.1.0"
        with self.assertRaisesRegex(RuntimeError, "single window"):
            desktop.validate_companion_nodes(self.nodes, self.labels)

    def test_label_or_disabled_button_cannot_supply_action_pass(self):
        for key, value in (("role", "label"), ("enabled", False), ("actions", [])):
            with self.subTest(key=key, value=value):
                nodes = [dict(node) for node in self.nodes]
                nodes[2][key] = value
                with self.assertRaisesRegex(RuntimeError, "actual Refresh status button"):
                    desktop.validate_companion_nodes(nodes, self.labels)

    def test_module_labels_respect_actual_runtime_state(self):
        for module, label in (({}, "Waiting"), ({"state": "waiting"}, "Waiting"),
                              ({"enabled": False}, "Disabled"),
                              ({"state": "disabled"}, "Disabled"),
                              ({"state": "ready"}, "Active"),
                              ({"state": "ready", "error": "failed"}, "Unavailable")):
            self.assertEqual(desktop.module_label(module), label)


if __name__ == "__main__":
    unittest.main()
