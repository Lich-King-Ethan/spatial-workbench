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


def module_label(module):
    """User-facing state expected from the real daemon's disconnected snapshot."""
    if not module:
        return "Waiting"
    if module.get("state") == "disabled" or module.get("enabled") is False:
        return "Disabled"
    if module.get("error") or module.get("state") in ("error", "unavailable", "failed"):
        return "Unavailable"
    if module.get("state") in ("active", "playing", "running", "ready"):
        return "Active"
    return "Waiting"


def validate_companion_nodes(nodes, labels):
    """Reject hidden, zero-size and out-of-window text, including the old blank applet."""
    windows = [node for node in nodes if node.get("role") in ("frame", "window", "dialog")
               and node.get("visible") and node.get("showing")
               and node.get("bounds") and node["bounds"][2] >= 200
               and node["bounds"][3] >= 120]
    for window in windows:
        matches = {}
        for label in labels:
            for node in nodes:
                if (node.get("name") != label or not node.get("visible")
                        or not node.get("showing") or not node.get("bounds")):
                    continue
                x, y, width, height = node["bounds"]
                if width <= 0 or height <= 0 or x < 0 or y < 0:
                    continue
                if (node["path"].startswith(window["path"] + ".")
                        and x + width <= window["bounds"][2] + 1
                        and y + height <= window["bounds"][3] + 1):
                    matches[label] = node
                    break
        if set(labels) != matches.keys():
            continue
        button = matches["Refresh status"]
        if button.get("role") not in ("push button", "button") or not button.get("enabled"):
            raise RuntimeError("Companion's actual Refresh status button is not enabled")
        if not button.get("actions"):
            raise RuntimeError("Companion's actual Refresh status button exports no action")
        return matches
    raise RuntimeError(f"Companion has no single window with visible in-window content for: {labels}")


def accessible_tree(application, atspi):
    nodes, objects = [], {}

    def visit(accessible, path, depth):
        if depth > 20 or len(nodes) >= 1000:
            raise RuntimeError("Companion accessibility tree exceeds the bounded inspection limit")
        state = accessible.getState()
        node = {"path": path, "name": accessible.name or "", "role": accessible.getRoleName(),
                "visible": state.contains(atspi.STATE_VISIBLE),
                "showing": state.contains(atspi.STATE_SHOWING),
                "enabled": state.contains(atspi.STATE_ENABLED)}
        try:
            rectangle = accessible.queryComponent().getExtents(atspi.WINDOW_COORDS)
            node["bounds"] = [rectangle.x, rectangle.y, rectangle.width, rectangle.height]
        except NotImplementedError:
            pass
        try:
            action = accessible.queryAction()
            node["actions"] = [action.getName(index) for index in range(action.nActions)]
        except NotImplementedError:
            pass
        nodes.append(node)
        objects[path] = accessible
        for index in range(accessible.childCount):
            child = accessible.getChildAtIndex(index)
            if child is not None:
                visit(child, f"{path}.{index}", depth + 1)

    visit(application, "app", 0)
    return nodes, objects


def application_for_pid(desktop, pid):
    applications = [desktop.getChildAtIndex(index) for index in range(desktop.childCount)]
    matches = [app for app in applications if app is not None and app.get_process_id() == pid]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one accessible application owned by PID {pid}")
    return matches[0]


def companion_accessibility(directory, process, timeout=45):
    """Inspect the installed window through the real session's AT-SPI bus."""
    import pyatspi
    from gi.repository import Atspi, GLib

    Atspi.set_timeout(2000, 5000)

    snapshot = json.loads(run("spatialctl", "status"))
    if snapshot.get("schema") != 1 or snapshot.get("connected") is not False:
        raise RuntimeError("This VM's disconnected UI check requires actual disconnected daemon state")
    runtime = snapshot.get("runtime", {})
    labels = ["No compatible headphones connected", "Spatial Audio is running",
              "Headphone output: Waiting", "Refresh status",
              "Application audio: " + module_label(runtime.get("live")),
              "Equalizer: " + module_label(runtime.get("equalizer"))]
    evidence = {"status": "fail", "pid": process.pid, "expected_labels": labels,
                "daemon_snapshot": snapshot, "nodes": []}
    deadline = time.monotonic() + timeout

    def inspect():
        if process.poll() is not None:
            raise RuntimeError("The Companion process exited during accessibility inspection")
        desktop = pyatspi.Registry.getDesktop(0)
        application = application_for_pid(desktop, process.pid)
        nodes, objects = accessible_tree(application, pyatspi)
        evidence["nodes"] = nodes
        return validate_companion_nodes(nodes, labels), objects

    def await_content():
        last_error = None
        while time.monotonic() < deadline:
            try:
                return inspect()
            except (RuntimeError, GLib.GError) as error:
                last_error = error
                time.sleep(0.25)
        raise RuntimeError(f"Installed Companion content did not become accessible: {last_error}")

    try:
        matches, objects = await_content()
        evidence["before_action"] = evidence["nodes"]
        button = objects[matches["Refresh status"]["path"]]
        action = button.queryAction()
        press_actions = [index for index in range(action.nActions)
                         if action.getName(index).lower() in ("press", "click")]
        if len(press_actions) != 1:
            raise RuntimeError("Refresh status does not expose one unambiguous press action")
        press = press_actions[0]
        # Invoke the real exported control, never a QML test hook or fake device.
        # This proves action acceptance and retained real status. The native Qt
        # test separately verifies the handler's D-Bus refresh transaction.
        if not action.doAction(press):
            raise RuntimeError("Companion rejected the actual Refresh status accessibility action")
        evidence["action"] = {"name": action.getName(press), "control": "Refresh status",
                              "accepted": True,
                              "scope": "actual control invocation; native Qt tests verify its D-Bus handler"}
        deadline = time.monotonic() + 10
        await_content()
        evidence["status"] = "pass"
        return {"file": "companion-accessibility.json", "pid": process.pid,
                "labels": labels, "refresh_action": evidence["action"]}
    except Exception as error:
        evidence["error"] = str(error)
        raise
    finally:
        (directory / "companion-accessibility.json").write_text(json.dumps(evidence, indent=2) + "\n")


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
            client_environment = dict(environment)
            if name == "companion":
                # Qt's documented accessibility opt-in applies only to the
                # real installed client; the Wayland session remains unchanged.
                client_environment["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
            processes.append((name, subprocess.Popen(command, env=client_environment,
                                                     stdout=log, stderr=subprocess.STDOUT)))
        accessibility = None
        content_error = None
        if installed:
            try:
                accessibility = companion_accessibility(directory, dict(processes)["companion"])
            except Exception as error:
                # Retain the real screenshot even when content admission fails.
                content_error = error
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
        if content_error is not None:
            raise content_error
        return {"file": screenshot.name, "size": size,
                "sha256": hashlib.sha256(screenshot.read_bytes()).hexdigest(),
                "windows": [name for name, _ in processes],
                "companion_accessibility": accessibility,
                "scope": "actual desktop rendering and installed disconnected content; native tests cover device controls"}
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
