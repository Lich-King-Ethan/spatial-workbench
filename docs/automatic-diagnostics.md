# Automatic local diagnostics

`spatial.health.Diagnostics` collects a local support report when a provider stays
in `error` or `unavailable` for at least two seconds. Ordinary waiting, disconnected
headphones and disabled modules do not trigger it. Repeated notifications for the
same module/state/detail count as one error episode. A recovery ends the episode;
a changed error can schedule another report after the global ten-minute cooldown.

Collection runs in its own child process using the installed
`spatial-diagnostics --output FILE`. It uses the same redacted, read-only collector
as the manual command. Automatic runs do **not** request journal logs, read login
or token files, upload anything, change routing, or restart audio components.
Provider error details are hashed for deduplication and are not passed to the
collector or copied into public diagnostic state.

Reports are stored in `$XDG_STATE_HOME/spatiald/diagnostics/`, normally
`~/.local/state/spatiald/diagnostics/`. The directory has mode 0700 and reports have
mode 0600. At most ten completed automatically named reports are retained; other
filenames and symlinks are left alone. A new run has a 60-second deadline, then
its owned process group is terminated and reaped. Service shutdown also cancels
collection and reaps it. Incomplete reports are removed.

Collection is enabled by default. Set `automatic_diagnostics = false` in
`~/.config/spatiald/config.toml` and restart `spatiald.service` to disable it.
Manual `spatial-diagnostics` and `spatial-verify` remain available.

Missing tools, filesystem errors, timeouts and collector failures affect only
this module's status. They do not trigger more automatic diagnostics or interrupt
playback. The same failed episode is not retried indefinitely. Status reports
collection readiness, attempts, report count and the most recent successful
report path; it does not include collector stdout/stderr or the triggering error
text. Review a report before sharing it.

The runtime integration API is:

```python
health = Diagnostics(enabled=True)
health.request("audio", "error", safe_public_error)
health.request("audio", "ready")  # clear the episode after recovery
snapshot = health.status()
await health.close()
```

Call `request` on the running event loop for both failures and recovery. It is
synchronous and schedules work without blocking a provider. Configuration and
provider error selection belong to the containing runtime. The constructor also
accepts an executable path, report directory and duration limits for integration
and isolated testing.

Tests run a real harmless collector subprocess to verify protected files,
deduplication, recovery, global throttling, scoped retention, failure isolation,
timeout and shutdown reaping. Their minimal JSON reports are test fixtures; no
user hardware or credentials are read during those tests.
