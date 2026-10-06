"""The native UI check must not activate desktop portals on a private bus."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "plasma/tests/run-private.sh"
CONFIG = ROOT / "plasma/tests/private-session.conf"


class NativeRunnerCleanupTests(unittest.TestCase):
    def run_fixture(self, exit_code, *, cleanup_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            # Only the wrapper's lifetime/cleanup contract is under test here.
            # The independent native-bus test below uses the real D-Bus daemon.
            launcher = binaries / "dbus-run-session"
            launcher.write_text('#!/bin/sh\nshift\ntest "$1" = -- || exit 99\nshift\nexec "$@"\n')
            launcher.chmod(0o755)
            if cleanup_failure:
                cleanup = binaries / "find"
                cleanup.write_text("#!/bin/sh\nexit 19\n")
                cleanup.chmod(0o755)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "keep.txt"
            sentinel.write_text("untouched")
            observed = root / "runtime.txt"
            program = (
                "import os, pathlib, sys; "
                "runtime = pathlib.Path(os.environ['XDG_RUNTIME_DIR']); "
                "pathlib.Path(sys.argv[1]).write_text(str(runtime)); "
                "(runtime / 'nested').mkdir(); "
                "(runtime / 'nested/file').write_text('test'); "
                "(runtime / 'outside-link').symlink_to(sys.argv[2], target_is_directory=True); "
                "sys.exit(int(sys.argv[3]))"
            )
            result = subprocess.run(
                ["bash", str(RUNNER), str(CONFIG), sys.executable, "-c", program,
                 str(observed), str(outside), str(exit_code)],
                env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                     "TMPDIR": str(root)}, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(sentinel.read_text(), "untouched")
            runtime = Path(observed.read_text())
            self.assertEqual(runtime.exists(), cleanup_failure)
            return result

    def test_nested_runtime_cleanup_preserves_command_exit_and_external_files(self):
        for exit_code in (0, 37):
            with self.subTest(exit_code=exit_code):
                result = self.run_fixture(exit_code)
                self.assertEqual(result.returncode, exit_code, result.stderr)

    def test_cleanup_error_cannot_turn_into_pass_or_hide_native_failure(self):
        for native_code, expected_code in ((0, 1), (37, 37)):
            with self.subTest(native_code=native_code):
                result = self.run_fixture(native_code, cleanup_failure=True)
                self.assertEqual(result.returncode, expected_code)
                self.assertIn("runtime cleanup failed", result.stderr)


@unittest.skipUnless(os.environ.get("SPATIAL_TEST_DBUS") == "1"
                     and shutil.which("dbus-run-session") and shutil.which("dbus-send"),
                     "needs explicitly enabled real D-Bus tests")
class PrivateSessionActivationTests(unittest.TestCase):
    def test_installed_service_is_visible_on_default_bus_but_cannot_activate_in_qml_bus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            services = root / "data/dbus-1/services"
            services.mkdir(parents=True)
            name = "org.spatiald.TestPortal"
            marker = root / "activated"
            (services / (name + ".service")).write_text(
                f"[D-BUS Service]\nName={name}\nExec=/usr/bin/touch {marker}\n"
            )
            env = {**os.environ, "XDG_DATA_HOME": str(root / "data"),
                   "XDG_DATA_DIRS": str(root / "empty"), "TMPDIR": str(root)}
            request = ["dbus-send", "--session", "--print-reply", "--reply-timeout=3000",
                       "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus"]
            # Prove this fixture is discoverable by the default bus used before
            # the fix; absence from the new bus alone would be a weak test.
            default = subprocess.run(
                ["dbus-run-session", "--", *request,
                 "org.freedesktop.DBus.ListActivatableNames"],
                env=env, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertIn(name, default.stdout)
            private = subprocess.run(
                ["bash", str(RUNNER), str(CONFIG), *request,
                 "org.freedesktop.DBus.StartServiceByName", "string:" + name, "uint32:0"],
                env=env, capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(private.returncode, 0)
            self.assertIn("org.freedesktop.DBus.Error.ServiceUnknown", private.stderr)
            self.assertFalse(marker.exists(), "The isolated check started an installed desktop service")
            self.assertFalse(list(root.glob("spatial-qml-runtime.*")))


if __name__ == "__main__":
    unittest.main()
