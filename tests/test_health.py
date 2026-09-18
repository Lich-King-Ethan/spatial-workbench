import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

from spatial.health import Diagnostics


class AutomaticDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.collector = self.base / "collector"
        self.started = self.base / "started"
        self.reports = self.base / "reports"
        self.instances = []
        self.helper()

    async def asyncTearDown(self):
        for instance in self.instances:
            await instance.close()
        self.temp.cleanup()

    def helper(self, *, sleep=0, exit_code=0):
        self.collector.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time\n"
            f"started = pathlib.Path({str(self.started)!r})\n"
            "with started.open('a') as handle: handle.write(str(os.getpid()) + '\\n')\n"
            "assert len(sys.argv) == 3 and sys.argv[1] == '--output'\n"
            f"time.sleep({sleep})\n"
            f"if {exit_code}: raise SystemExit({exit_code})\n"
            "pathlib.Path(sys.argv[2]).write_text(json.dumps({'schema': 1, 'mode': 'read-only'}))\n")
        self.collector.chmod(0o700)

    def diagnostics(self, **kwargs):
        parameters = dict(executable=str(self.collector), report_dir=self.reports,
                          settle_delay=0.02, cooldown=0.02, timeout=2)
        parameters.update(kwargs)
        instance = Diagnostics(**parameters)
        self.instances.append(instance)
        return instance

    async def until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("condition not reached within timeout")
            await asyncio.sleep(0.005)

    def invocations(self):
        return self.started.read_text().splitlines() if self.started.exists() else []

    async def test_real_collector_private_redacted_report_and_deduplication(self):
        instance = self.diagnostics()
        instance.request("audio", "error", "https://example.invalid/?token=SECRET")
        await self.until(lambda: instance.status()["state"] == "ready")
        report = Path(instance.status()["last_report"])
        self.assertEqual(stat.S_IMODE(self.reports.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o600)
        self.assertEqual(json.loads(report.read_text())["mode"], "read-only")
        for _ in range(10):
            instance.request("audio", "error", "https://example.invalid/?token=SECRET")
        await asyncio.sleep(0.04)
        self.assertEqual(len(self.invocations()), 1)
        self.assertNotIn("SECRET", str(instance.status()))
        self.assertFalse(instance.status()["includes_logs"])
        self.assertFalse(instance.status()["uploads"])
        instance.request("audio", "ready")
        instance.request("audio", "error", "https://example.invalid/?token=SECRET")
        await self.until(lambda: len(self.invocations()) == 2 and not instance.status()["running"])

    async def test_waiting_disabled_and_transient_errors_do_not_collect(self):
        instance = self.diagnostics(settle_delay=0.1)
        instance.request("audio", "waiting", "Headphones disconnected")
        instance.request("sensor", "unavailable", "temporary")
        instance.request("sensor", "ready")
        instance.request("diagnostics", "error", "must not recurse")
        disabled = self.diagnostics(enabled=False)
        disabled.request("audio", "error", "disabled")
        await asyncio.sleep(0.12)
        self.assertFalse(self.started.exists())
        self.assertFalse(self.reports.exists())

    async def test_global_cooldown_delays_other_module_and_retention_is_scoped(self):
        instance = self.diagnostics(cooldown=0.15, max_reports=2)
        self.reports.mkdir()
        unrelated = self.reports / "auto-my-manual-report.json"
        unrelated.write_text("keep")
        for i in range(3):
            instance.request(f"module-{i}", "error", "fault")
            await self.until(lambda: instance.status()["attempts"] == i + 1 and instance.status()["state"] == "ready")
            if i == 0:
                instance.request("module-1", "error", "fault")
                await asyncio.sleep(0.04)
                self.assertEqual(len(self.invocations()), 1)
        self.assertEqual(len(self.invocations()), 3)
        self.assertEqual(instance.status()["report_count"], 2)
        self.assertEqual(len(list(self.reports.glob("*.json"))), 3)
        self.assertEqual(unrelated.read_text(), "keep")

    async def test_failed_collector_does_not_retry_itself(self):
        self.helper(exit_code=9)
        instance = self.diagnostics()
        instance.request("tidal", "unavailable", "no source")
        await self.until(lambda: instance.status()["state"] == "unavailable")
        self.assertFalse(list(self.reports.glob("*.json")))
        instance.request("tidal", "unavailable", "no source")
        await asyncio.sleep(0.04)
        self.assertEqual(len(self.invocations()), 1)

    async def test_missing_collector_is_nonfatal(self):
        instance = self.diagnostics(executable=str(self.base / "absent"))
        instance.request("audio", "error", "fault")
        await self.until(lambda: instance.status()["state"] == "unavailable")
        self.assertEqual(instance.status()["attempts"], 1)
        self.assertFalse(self.reports.exists())

    async def test_timeout_and_close_reap_actual_child(self):
        self.helper(sleep=30)
        instance = self.diagnostics(timeout=0.07)
        instance.request("audio", "error", "fault")
        await self.until(lambda: self.started.exists())
        pid = int(self.invocations()[-1])
        await self.until(lambda: instance.status()["state"] == "unavailable" and not instance.status()["running"])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(list(self.reports.glob("*.json")))
        other = self.diagnostics()
        other.request("audio", "error", "another fault")
        await self.until(lambda: len(self.invocations()) == 2)
        second_pid = int(self.invocations()[-1])
        await other.close()
        with self.assertRaises(ProcessLookupError):
            os.kill(second_pid, 0)
        self.assertEqual(other.status()["state"], "stopped")

    async def test_symlink_directory_refused_without_changing_destination(self):
        target = self.base / "destination"
        target.mkdir(mode=0o755)
        self.reports.symlink_to(target, target_is_directory=True)
        instance = self.diagnostics()
        instance.request("audio", "error", "fault")
        await self.until(lambda: instance.status()["state"] == "unavailable")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertFalse(self.started.exists())
