"""Bounded shell fixtures: no package manager or privileged operation is run."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] / "build.sh").read_text()


def function(name):
    match = re.search(rf"(?ms)^{name}\(\) \{{\n.*?^\}}", SCRIPT)
    if match is None:
        raise AssertionError(f"Installer function missing: {name}")
    return match.group()


class InstallerContractTests(unittest.TestCase):
    def run_logged(self, command):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build.log"
            program = "\n".join([
                "set -uo pipefail", "umask 077",
                "spatial_green=''; spatial_reset=''; spatial_current_step=''",
                function("message"), function("passed"), function("run_logged_step"),
                f"run_logged_step 'Compile fixture' {command}",
            ])
            result = subprocess.run(
                ["bash", "-c", program], text=True, capture_output=True,
                env={**os.environ, "spatial_install_log": str(log)}, timeout=10,
            )
            return result, log.read_text(), log.stat().st_mode & 0o777

    def test_compiler_output_is_private_and_success_is_real(self):
        result, log, mode = self.run_logged("bash -c 'echo compiler-detail; echo warning-detail >&2'")
        self.assertEqual(result.returncode, 0)
        self.assertIn("[PASS] Compile fixture", result.stdout)
        self.assertNotIn("compiler-detail", result.stdout)
        self.assertNotIn("warning-detail", result.stderr)
        self.assertIn("compiler-detail", log)
        self.assertIn("warning-detail", log)
        self.assertEqual(mode, 0o600)

    def test_build_failure_preserves_error_code_without_green_success(self):
        result, log, _ = self.run_logged("bash -c 'echo actual-build-error >&2; exit 37'")
        self.assertEqual(result.returncode, 37)
        self.assertNotIn("[PASS]", result.stdout)
        self.assertIn("actual-build-error", log)

    def test_reuse_requires_exact_package_and_satisfying_version(self):
        for exact_name, version_satisfied, expected in [(1, 1, 0), (0, 1, 1), (1, 0, 1)]:
            with self.subTest(exact_name=exact_name, version=version_satisfied):
                program = "\n".join([
                    "pacman() {",
                    '  case "$1" in',
                    f'    -Q) [[ "$2" == mpv-omniphony && {exact_name} == 1 ]] ;;',
                    f'    -T) [[ "$2" == "mpv-omniphony>=0.5.2-1" && {version_satisfied} == 1 ]] ;;',
                    "    *) return 99 ;;", "  esac", "}",
                    function("installed_aur_satisfies"),
                    "installed_aur_satisfies mpv-omniphony 'mpv-omniphony>=0.5.2-1'",
                ])
                result = subprocess.run(["bash", "-c", program], timeout=10)
                self.assertEqual(result.returncode, expected)


if __name__ == "__main__":
    unittest.main()
