"""Bounded, local-only diagnostic collection for persistent error episodes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import stat
import time
import uuid

from .processes import stop_child


@dataclass
class _Episode:
    fingerprint: str
    since: float
    attempted: bool = False


class Diagnostics:
    """Own a diagnostic subprocess independently of audio and sensor providers.

    Call request for recovery states too: a later recurrence is a new episode.
    Detail text is used only for a digest; it is never put in arguments or status.
    """

    def __init__(self, *, enabled=True, executable="spatial-diagnostics", report_dir=None,
                 cooldown=600.0, settle_delay=2.0, timeout=60.0, max_reports=10):
        for name, value in (("cooldown", cooldown), ("settle_delay", settle_delay), ("timeout", timeout)):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative duration")
        if timeout <= 0 or not isinstance(max_reports, int) or not 1 <= max_reports <= 10:
            raise ValueError("timeout must be positive and max_reports must be between 1 and 10")
        self.enabled = bool(enabled)
        self.executable = str(executable)
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        self.report_dir = Path(report_dir) if report_dir is not None else base / "spatiald/diagnostics"
        self.cooldown, self.settle_delay, self.timeout = cooldown, settle_delay, timeout
        self.max_reports = max_reports
        self._episodes = {}
        self._task = None
        self._wake = asyncio.Event()
        self._closed = False
        self._last_started = None
        self._process = None
        self._state = "idle" if self.enabled else "disabled"
        self._reason = "No persistent errors observed" if self.enabled else "Automatic diagnostics disabled"
        self._last_report = None
        self._report_count = 0
        self._attempts = 0

    def request(self, module, state, detail=""):
        """Schedule an error report without blocking or raising into a provider."""
        if not self.enabled or self._closed:
            return
        if not isinstance(module, str) or not module or module in ("diagnostics", "automatic_diagnostics", "health"):
            return
        if state not in ("error", "unavailable"):
            self._episodes.pop(module, None)
            self._wake.set()
            return
        # No provider detail, URLs or identifiers are retained in public state.
        fingerprint = hashlib.sha256((str(state) + "\0" + str(detail)).encode("utf-8", "replace")).hexdigest()
        previous = self._episodes.get(module)
        if previous is None or previous.fingerprint != fingerprint:
            self._episodes[module] = _Episode(fingerprint, time.monotonic())
        self._wake.set()
        if self._task is None or self._task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._state, self._reason = "unavailable", "Automatic diagnostics require the running service"
                return
            self._task = loop.create_task(self._drive(), name="automatic-diagnostics")

    def status(self):
        return {"enabled": self.enabled, "state": self._state, "reason": self._reason,
                "running": self._process is not None and self._process.returncode is None,
                "attempts": self._attempts, "report_count": self._report_count,
                "last_report": self._last_report, "uploads": False, "includes_logs": False}

    async def _drive(self):
        try:
            while not self._closed:
                pending = [episode for episode in self._episodes.values() if not episode.attempted]
                if not pending:
                    if self._state == "waiting":
                        self._state, self._reason = "idle", "No persistent errors observed"
                    return
                episode = min(pending, key=lambda item: item.since)
                due = episode.since + self.settle_delay
                if self._last_started is not None:
                    due = max(due, self._last_started + self.cooldown)
                delay = due - time.monotonic()
                if delay > 0:
                    self._state = "waiting"
                    self._reason = "Waiting for a persistent error or diagnostic cooldown"
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), delay)
                    except TimeoutError:
                        pass
                    continue
                # Mark before launching: a missing/broken collector never retries itself.
                episode.attempted = True
                self._last_started = time.monotonic()
                self._attempts += 1
                await self._collect()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Diagnostic failures must not recursively generate more diagnostics.
            self._state, self._reason = "unavailable", "Automatic diagnostic collection failed"

    def _directory(self):
        self.report_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.report_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("diagnostic directory is not an owned regular directory")
        self.report_dir.chmod(0o700, follow_symlinks=False)

    def _reports(self):
        reports = []
        for path in self.report_dir.glob("auto-*.json"):
            if not re.fullmatch(r"auto-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{32}\.json", path.name):
                continue
            info = path.lstat()
            # Never follow or remove symlinks, directories or another owner's file.
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                reports.append((info.st_mtime_ns, path))
        return sorted(reports, key=lambda item: (item[0], str(item[1])))

    def _prune(self):
        reports = self._reports()
        for _, path in reports[:-self.max_reports]:
            path.unlink()
        self._report_count = min(len(reports), self.max_reports)

    def _validate_report(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 8 * 1024 * 1024:
                raise OSError("invalid diagnostic report")
            os.fchmod(stream.fileno(), 0o600)
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("mode") != "read-only":
            raise ValueError("invalid diagnostic report schema")

    async def _collect(self):
        executable = shutil.which(self.executable)
        if executable is None:
            self._state, self._reason = "unavailable", "spatial-diagnostics is not installed"
            return
        output = None
        succeeded = False
        try:
            self._directory()
            self._prune()
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            output = self.report_dir / f"auto-{timestamp}-{uuid.uuid4().hex}.json"
            self._state, self._reason = "collecting", "Collecting a local redacted diagnostic report"
            self._process = await asyncio.create_subprocess_exec(
                executable, "--output", str(output),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            try:
                code = await asyncio.wait_for(self._process.wait(), self.timeout)
            except TimeoutError:
                self._state, self._reason = "unavailable", "Automatic diagnostic collection timed out"
                return
            if code != 0:
                self._state, self._reason = "unavailable", "Diagnostic collector returned an error"
                return
            self._validate_report(output)
            self._prune()
            self._last_report = str(output)
            self._state, self._reason = "ready", "Local redacted diagnostic report saved"
            succeeded = True
        except (OSError, ValueError):
            self._state, self._reason = "unavailable", "Could not create a private diagnostic report"
        finally:
            if self._process is not None:
                await stop_child(self._process)
                self._process = None
            if output is not None and not succeeded:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass

    async def close(self):
        self._closed = True
        self._episodes.clear()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._state, self._reason = "stopped", "Automatic diagnostics stopped"
