"""Bounded shell fixtures: no package manager or privileged operation is run."""
import os
import hashlib
from pathlib import Path
import re
import shlex
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

    def test_audio_preflight_rejects_incompatible_installed_group(self):
        cases = [({}, True),
                 ({"harletty-bridge": "0.7.3-1", "mpv-omniphony": "0.5.2-1",
                   "orender-spatial": "0.5.2-2"}, True),
                 ({"harletty-bridge": "0.8.0-1"}, False),
                 ({"harletty-bridge": "0.7.2-1"}, False),
                 ({"mpv-omniphony": "0.6.0-1"}, False),
                 ({"mpv-omniphony": "0.5.2-2"}, False),
                 ({"orender-spatial": "0.6.0-1"}, False),
                 ({"orender": "0.6.0-1"}, False),
                 ({"orender": "0.5.2-1"}, True)]
        for packages, compatible in cases:
            with self.subTest(packages=packages):
                branches = [f'{shlex.quote(name)}) printf "%s\\n" {shlex.quote(name + " " + version)} ;;'
                            for name, version in packages.items()]
                program = "\n".join([
                    "set -euo pipefail",
                    'message() { printf "%s\\n" "$*"; }',
                    'pacman() { [[ "$1" == -Q ]] || exit 99; case "$2" in',
                    *branches, '*) return 1 ;;', 'esac; }',
                    # This fixture only needs the same-version upstream case;
                    # Arch's actual versioned conflicts run in package builds.
                    'vercmp() { if [[ "$1" == 0.5.2-* ]]; then echo 0; else echo 1; fi; }',
                    function("pinned_aur_recipe"), function("installed_package_version"),
                    function("preflight_audio_stack"), "preflight_audio_stack",
                ])
                result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0 if compatible else 1, result.stderr)
                if not compatible:
                    self.assertIn("No package transaction has started", result.stdout)

    def test_audio_pins_use_same_path_with_yay_paru_or_no_helper(self):
        for helper in ("yay", "paru", None):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as empty_path:
                program = "\n".join([
                    "set -euo pipefail", "message() { :; }",
                    'build_aur_package() { printf "build:%s\\n" "$1"; }',
                    'run_step() { shift; "$@"; }',
                    f'{helper}() {{ printf "helper:%s\\n" "$*"; }}' if helper else ":",
                    function("install_audio_modules"), "install_audio_modules",
                ])
                result = subprocess.run(["/bin/bash", "-c", program], capture_output=True, text=True,
                                        env={**os.environ, "PATH": empty_path}, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), ["build:harletty-bridge", "build:mpv-omniphony",
                    "helper:-S --needed sony-tracker" if helper else "build:sony-tracker"])

    def pinned_recipe_fixture(self, *, altered_hash=False, missing_files=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream"
            upstream.mkdir()
            def git(*arguments):
                return subprocess.check_output(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                                                "-C", str(upstream), *arguments], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            git("init", "--quiet")
            recipe = upstream / "PKGBUILD"
            metadata = upstream / ".SRCINFO"
            recipe.write_text("# reviewed historical recipe\n")
            metadata.write_text("pkgbase = mpv-omniphony\n\tpkgver = 0.5.2\n\tpkgrel = 1\npkgname = mpv-omniphony\n")
            git("add", "PKGBUILD", ".SRCINFO")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "reviewed")
            commit = git("rev-parse", "HEAD")
            recipe_hash = hashlib.sha256(recipe.read_bytes()).hexdigest()
            metadata_hash = hashlib.sha256(metadata.read_bytes()).hexdigest()
            recipe.write_text("# incompatible future recipe\n")
            git("add", "PKGBUILD")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "future")
            specification = f'{commit} 0.5.2-1 {"0" * 64 if altered_hash else recipe_hash} {metadata_hash}'
            log = root / "install.log"
            program = "\n".join([
                "set -Eeuo pipefail", "spatial_green=''; spatial_reset=''", "package_files=()",
                function("message"), function("passed"), function("run_logged_step"),
                function("installed_package_version"), function("installed_aur_satisfies"),
                f'pinned_aur_recipe() {{ printf "%s\\n" {shlex.quote(specification)}; }}',
                # Redirect only clone's public fixture URL to this actual local
                # Git history; checkout/rev-parse/log execute the real Git code.
                'git() { if [[ "$1" == clone ]]; then command git clone --no-checkout "$fixture_upstream" "${@: -1}"; else command git "$@"; fi; }',
                'pacman() { case "$1" in',
                '  -Q) printf "%s\\n" "mpv-omniphony 0.5.2-1" ;;',
                f'  -Qk) return {17 if missing_files else 0} ;;',
                '  *) return 99 ;; esac; }',
                'build_package() { echo "Unexpected build" >&2; return 98; }',
                function("build_aur_package"), "build_aur_package mpv-omniphony",
            ])
            result = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=10,
                                    env={**os.environ, "fixture_upstream": str(upstream),
                                         "spatial_install_dir": str(root), "spatial_install_log": str(log)})
            checked_out = next(root.glob("aur-mpv-omniphony.*/source"))
            self.assertEqual(subprocess.check_output(["git", "-C", str(checked_out), "rev-parse", "HEAD"],
                                                    text=True).strip(), commit)
            return result, log.read_text()

    def test_pinned_recipe_checks_out_history_and_reuses_only_intact_exact_package(self):
        result, log = self.pinned_recipe_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Reuse installed mpv-omniphony satisfying mpv-omniphony=0.5.2-1", result.stdout)
        self.assertIn("PKGBUILD: OK", log)
        self.assertIn(".SRCINFO: OK", log)

    def test_modified_recipe_hash_is_rejected_before_reuse(self):
        result, log = self.pinned_recipe_fixture(altered_hash=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED", log)
        self.assertNotIn("Reuse installed", result.stdout)

    def test_missing_installed_package_files_are_not_reused(self):
        result, _ = self.pinned_recipe_fixture(missing_files=True)
        self.assertEqual(result.returncode, 17)
        self.assertNotIn("Reuse installed", result.stdout)


if __name__ == "__main__":
    unittest.main()
