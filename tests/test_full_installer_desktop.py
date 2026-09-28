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


if __name__ == "__main__":
    unittest.main()
