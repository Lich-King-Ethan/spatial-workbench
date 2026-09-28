#!/usr/bin/env python3
"""Require a real local Plasma Wayland login and retain desktop evidence.

This runs as the disposable VM's desktop user. An offscreen Qt process or a
separate dbus-run-session is not a substitute for the SDDM/logind session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import time


COMPANION = "com.github.maniacx.BudsLink-Companion"
ENVIRONMENT_KEYS = (
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE", "KDE_FULL_SESSION", "EDITOR", "VISUAL",
    "LIBGL_ALWAYS_SOFTWARE", "QT_QUICK_BACKEND",
)


def run(*command, timeout=15, env=None):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT,
                                   timeout=timeout, env=env)


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def is_desktop_session(session):
    return (session.get("User") == "1000" and session.get("Active") == "yes"
            and session.get("Remote") == "no" and session.get("Type") == "wayland"
            and session.get("Class") == "user" and session.get("Seat") == "seat0"
            and session.get("Service") == "sddm-autologin")


def imported_environment(text):
    environment = {}
    for line in text.splitlines():
        parts = shlex.split(line)
        if len(parts) == 1 and "=" in parts[0]:
            name, value = parts[0].split("=", 1)
            if name in ENVIRONMENT_KEYS:
                environment[name] = value
    if environment.get("XDG_SESSION_TYPE") != "wayland":
        raise RuntimeError("The real user manager has no Wayland desktop environment")
    if "KDE" not in environment.get("XDG_CURRENT_DESKTOP", "").split(":"):
        raise RuntimeError("The real user manager has no KDE desktop environment")
    display = environment.get("WAYLAND_DISPLAY", "")
    if not display or Path(display).name != display:
        raise RuntimeError("The user manager did not import a local Wayland socket name")
    if any(environment.get(key) != "nano" for key in ("EDITOR", "VISUAL")):
        raise RuntimeError("The desktop did not inherit nano as EDITOR and VISUAL")
    return environment


def desktop_state():
    sessions = []
    for line in run("loginctl", "list-sessions", "--no-legend", "--no-pager").splitlines():
        if not line.strip():
            continue
        session_id = line.split()[0]
        session = properties(run("loginctl", "show-session", session_id,
                                 "-p", "User", "-p", "Active", "-p", "Remote",
                                 "-p", "Type", "-p", "Class", "-p", "Seat",
                                 "-p", "Service", "-p", "Desktop", "-p", "Leader"))
        if is_desktop_session(session):
            sessions.append({"id": session_id, **session})
    if len(sessions) != 1:
        raise RuntimeError(f"Expected one active SDDM Wayland desktop session, found {sessions}")
    environment = imported_environment(run("systemctl", "--user", "show-environment"))
    if not (Path(os.environ["XDG_RUNTIME_DIR"]) / environment["WAYLAND_DISPLAY"]).is_socket():
        raise RuntimeError("The imported Wayland display is not a live Unix socket")
    services = {}
    for service, expected_program in (("org.kde.KWin", "kwin_wayland"),
                                      ("org.kde.plasmashell", "plasmashell")):
        response = run("busctl", "--user", "call", "org.freedesktop.DBus",
                       "/org/freedesktop/DBus", "org.freedesktop.DBus",
                       "GetConnectionUnixProcessID", "s", service).strip().split()
        if len(response) != 2 or response[0] != "u":
            raise RuntimeError(f"Invalid desktop service PID response: {response}")
        process = Path("/proc") / str(int(response[1]))
        if (process / "exe").resolve().name != expected_program or process.stat().st_uid != 1000:
            raise RuntimeError(f"{service} is not owned by the real desktop process")
        services[service] = {"pid": int(response[1]), "program": expected_program}
    connected = [str(path.parent) for path in Path("/sys/class/drm").glob("card*-*/status")
                 if path.read_text().strip() == "connected"]
    if not connected:
        raise RuntimeError("No connected kernel DRM output; refusing a headless desktop PASS")
    return {"session": sessions[0], "services": services, "connected_drm_outputs": connected,
            "environment": environment}


def await_desktop(timeout=150):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return desktop_state()
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"Plasma Wayland did not become ready: {last_error}")


def drive_nano(child, path, text, timeout=15):
    """Save through the actual editor; judge bytes, not curses prompt wording."""
    import pexpect

    if path.exists() or path.is_symlink():
        raise RuntimeError("The nano acceptance file must not exist before editing")
    expected = (text + "\n").encode("utf-8")
    child.expect("GNU nano")
    child.send(text)
    # Ctrl+S saves the named file directly. Ctrl+O is Save As, whose prompt
    # wording/curses redraws vary between nano releases and locales.
    # https://www.nano-editor.org/dist/latest/cheatsheet.html
    child.sendcontrol("s")
    deadline = time.monotonic() + timeout
    while True:
        if path.is_file() and path.read_bytes() == expected:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("nano did not save the exact expected bytes before the deadline")
        try:
            # Drain terminal output into the retained transcript while waiting
            # for nano's disk write. Nothing outside the editor writes the file.
            child.read_nonblocking(size=4096, timeout=min(0.2, max(0, deadline - time.monotonic())))
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF as error:
            raise RuntimeError("nano exited before saving the expected file") from error
    child.sendcontrol("x")
    child.expect(pexpect.EOF)
    child.close()
    if child.exitstatus != 0 or path.read_bytes() != expected:
        raise RuntimeError("nano did not exit successfully with the exact saved content")
    return hashlib.sha256(expected).hexdigest()


def nano_check(directory, environment):
    import pexpect

    path = directory / "nano-edit.txt"
    text = "Saved by the real nano editor launched from login fish."
    with (directory / "nano-terminal.log").open("w") as transcript:
        child = pexpect.spawn("/usr/bin/fish", ["--login", "--command",
                               'exec $EDITOR --ignorercfiles "$argv[1]"', str(path)],
                              env={**environment, "TERM": "xterm-256color"},
                              encoding="utf-8", timeout=15, dimensions=(30, 120))
        child.logfile_read = transcript
        try:
            saved_sha256 = drive_nano(child, path, text)
        finally:
            if child.isalive():
                child.terminate(force=True)
    return {"editor": "nano", "invoked_from": "fish --login", "saved_file": path.name,
            "sha256": saved_sha256, "transcript": "nano-terminal.log"}


def qml_failures(text):
    pattern = re.compile(
        r"Error loading (?:the )?QML file|QQmlApplicationEngine failed to load|"
        r"Type [\w.]+ unavailable|module .+ is not installed|\b\w+ is not a type|"
        r"\b(?:ReferenceError|TypeError|SyntaxError):", re.IGNORECASE)
    return [line for line in text.splitlines() if pattern.search(line)]


def screenshot_evidence(directory, environment, installed):
    from PIL import Image, ImageStat

    processes = []
    logs = []
    terminal_command = (
        "printf '%s\\n' 'CachyOS Plasma Wayland: real booted KVM desktop' "
        "'Login shell and editor:'; fish --version; nano --version | head -n 1; "
        "printf 'EDITOR=%s VISUAL=%s\\n' $EDITOR $VISUAL; "
        + ("spatialctl status; " if installed else "")
        + "sleep 90"
    )
    commands = [("konsole", ["konsole", "--separate", "--nofork", "-e", "fish",
                             "--login", "--command", terminal_command])]
    if installed:
        commands.append(("companion", ["plasmawindowed", COMPANION]))
    try:
        for name, command in commands:
            log = (directory / f"{name}.log").open("w")
            logs.append(log)
            processes.append((name, subprocess.Popen(command, env=environment,
                                                     stdout=log, stderr=subprocess.STDOUT)))
        # Spectacle delays its capture while the actual Wayland windows render.
        # Waking the virtual output does not bypass the session/service checks.
        run("kscreen-doctor", "--dpms", "on", env=environment)
        screenshot = directory / "desktop.png"
        run("spectacle", "--background", "--nonotify", "--fullscreen", "--delay", "3000",
            "--output", str(screenshot), timeout=45, env=environment)
        for name, process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"The actual {name} window exited before desktop capture")
        if installed:
            errors = qml_failures((directory / "companion.log").read_text())
            if errors:
                raise RuntimeError(f"The installed Companion reported QML errors: {errors[:10]}")
        with Image.open(screenshot) as captured:
            captured.load()
            if captured.format != "PNG" or captured.width < 640 or captured.height < 480:
                raise RuntimeError("Desktop screenshot is missing or too small")
            deviation = ImageStat.Stat(captured.convert("RGB")).stddev
            if max(deviation) < 8:
                raise RuntimeError("Desktop screenshot is blank or nearly uniform")
            size = [captured.width, captured.height]
        return {"file": screenshot.name, "size": size,
                "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
                "windows": [name for name, _ in processes],
                "scope": "actual desktop rendering; individual widget actions require separate checks"}
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 1000 or os.environ.get("XDG_RUNTIME_DIR") != "/run/user/1000":
        raise RuntimeError("Run as the disposable VM's desktop user")
    if pwd.getpwuid(os.geteuid()).pw_shell != "/usr/bin/fish":
        raise RuntimeError("The desktop account's login shell is not fish")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report = {"status": "fail", "installed_application": args.installed}
    try:
        report.update(await_desktop())
        environment = {**os.environ, **report["environment"], "QT_QPA_PLATFORM": "wayland"}
        report["shell"] = run("fish", "--login", "--command",
                              "test $EDITOR = nano; and test $VISUAL = nano; and fish --version",
                              env=environment).strip()
        report["editor"] = nano_check(args.report_dir, environment)
        report["screenshot"] = screenshot_evidence(args.report_dir, environment, args.installed)
        (args.report_dir / "kwin-support.txt").write_text(run(
            "busctl", "--user", "call", "org.kde.KWin", "/KWin", "org.kde.KWin",
            "supportInformation"))
        report["status"] = "pass"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (args.report_dir / "desktop.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
