"""Run the real Arch staging guard without installing packages or needing pip."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


RECIPE = Path(__file__).resolve().parents[1] / "packaging/PKGBUILD.in"


class CorePackagingTests(unittest.TestCase):
    def valid_tree(self, root):
        module = root / "usr/lib/python3.14/site-packages/spatial/__init__.py"
        module.parent.mkdir(parents=True)
        module.write_text('"""Package layout fixture."""\n')
        for name in ("spatialctl", "budslink-spatial"):
            launcher = root / "usr/bin" / name
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text("#!/usr/bin/python\nprint('layout fixture')\n")
            launcher.chmod(0o755)

    def validate(self, root):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; pkgdir="$2"; _validate_python_layout',
             "core-layout-fixture", str(RECIPE), str(root)],
            text=True, capture_output=True, timeout=10,
            env={**os.environ, "VIRTUAL_ENV": "/home/builder/active-venv"},
        )

    def test_system_layout_passes_with_an_active_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.valid_tree(root)
            result = self.validate(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_virtual_environment_payload_outside_usr_is_rejected(self):
        for prefix in ("home/builder/project/.venv", "opt/venv", ".unexpected"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.valid_tree(root)
                stray = root / prefix / "bin/spatialctl"
                stray.parent.mkdir(parents=True)
                stray.write_text("#!/home/builder/project/.venv/bin/python\n")
                result = self.validate(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("entirely under /usr", result.stderr)

    def test_launcher_interpreter_and_executable_are_required_for_both_aliases(self):
        for name in ("spatialctl", "budslink-spatial"):
            for defect in ("venv-shebang", "env-shebang", "not-executable", "missing", "symlink"):
                with self.subTest(name=name, defect=defect), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.valid_tree(root)
                    launcher = root / "usr/bin" / name
                    if defect == "venv-shebang":
                        launcher.write_text("#!/home/builder/project/.venv/bin/python\n")
                    elif defect == "env-shebang":
                        launcher.write_text("#!/usr/bin/env python\n")
                    elif defect == "not-executable":
                        launcher.chmod(0o644)
                    elif defect == "missing":
                        launcher.unlink()
                    else:
                        launcher.unlink()
                        launcher.symlink_to("budslink-spatial" if name == "spatialctl" else "spatialctl")
                    result = self.validate(root)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)

    def test_missing_or_non_system_module_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.valid_tree(root)
            module = root / "usr/lib/python3.14/site-packages/spatial/__init__.py"
            misplaced = root / "usr/local/lib/python3.14/site-packages/spatial/__init__.py"
            misplaced.parent.mkdir(parents=True)
            module.rename(misplaced)
            result = self.validate(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("system Python module", result.stderr)


if __name__ == "__main__":
    unittest.main()
