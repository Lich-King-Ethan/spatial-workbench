"""The real desktop gate must reject offscreen, greeter and remote substitutes."""
import importlib.util
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
