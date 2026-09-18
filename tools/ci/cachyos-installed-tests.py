#!/usr/bin/env python3
"""Require the real installed package and all tests; absence and skips fail CI."""
import argparse
import ctypes.util
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "failed", "scope": "installed-package tests on a private real D-Bus"}
    try:
        if os.geteuid() == 0:
            raise RuntimeError("Tests must run as the unprivileged builder")
        if os.environ.get("SPATIAL_TEST_DBUS") != "1" or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            raise RuntimeError("A real dbus-run-session and SPATIAL_TEST_DBUS=1 are required")
        if not sys.flags.isolated:
            raise RuntimeError("Use python -I to exclude source-tree imports")

        import spatial
        from spatial.equalizer import find_limiter

        root = Path(__file__).resolve().parents[2]
        installed = Path(spatial.__file__).resolve()
        if installed.is_relative_to(root):
            raise RuntimeError(f"Imported the checkout instead of the package: {installed}")
        owner = subprocess.check_output(["pacman", "-Qqo", str(installed)], text=True).strip()
        if owner != "spatial-workbench":
            raise RuntimeError(f"Unexpected owner of installed Python module: {owner!r}")
        for executable in ("node", "cc", "luac", "dbus-daemon", "orender", "pw-dump", "wpctl"):
            if not shutil.which(executable):
                raise RuntimeError(f"Native dependency is missing: {executable}")
        if not any(ctypes.util.find_library(name) for name in ("lua5.5", "lua5.4", "lua5.3", "lua")):
            raise RuntimeError("Lua shared library is missing")
        report.update(module_path=str(installed), package_owner=owner,
                      version=importlib.metadata.version("spatial-workbench"),
                      limiter=str(find_limiter()))
        subprocess.run(["spatialctl", "--version"], check=True)
        subprocess.run(["pacman", "-Qk", "spatial-workbench",
                        "plasma-budslink-companion-spatial", "orender-spatial"], check=True)
        suite = unittest.defaultTestLoader.discover(str(root / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        report.update(tests_run=result.testsRun,
                      skipped=[{"test": str(test), "reason": reason} for test, reason in result.skipped],
                      failures=[{"test": str(test), "traceback": detail} for test, detail in result.failures],
                      errors=[{"test": str(test), "traceback": detail} for test, detail in result.errors])
        # Test helpers can modify sys.path after startup. Check every loaded
        # submodule as well as the initial package, so -I cannot give false
        # confidence while a late import silently comes from the checkout.
        modules = {}
        for name, module in tuple(sys.modules.items()):
            if name == "spatial" or name.startswith("spatial."):
                module_file = getattr(module, "__file__", None)
                if not module_file:
                    raise RuntimeError(f"Cannot establish the installed origin of {name}")
                path = Path(module_file).resolve()
                if not path.is_relative_to(installed.parent):
                    raise RuntimeError(f"Imported {name} outside the installed package: {path}")
                modules[name] = str(path)
        owners = subprocess.check_output(
            ["pacman", "-Qqo", *sorted(set(modules.values()))], text=True).splitlines()
        if not owners or set(owners) != {"spatial-workbench"}:
            raise RuntimeError(f"Unexpected owners of installed spatial modules: {owners!r}")
        if any(Path(entry).resolve() == root for entry in sys.path if entry):
            raise RuntimeError("A test helper reinserted the source root into the isolated import path")
        report["verified_installed_modules"] = modules
        if not result.testsRun or not result.wasSuccessful() or result.skipped:
            raise RuntimeError("Installed-package suite failed, was empty, or skipped native tests")
        report["status"] = "passed"
    except Exception:
        report["exception"] = traceback.format_exc()
        print(report["exception"], file=sys.stderr)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
