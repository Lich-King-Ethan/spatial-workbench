#!/usr/bin/env python3
"""Answer reviewed installation prompts inside the disposable integration VM.

The real installer and package managers keep their terminal, code and prompts.
Only known confirmations receive input; an unexpected prompt is a test failure.
"""
from pathlib import Path
import argparse
import os
import sys
import time

import pexpect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-only', action='store_true',
                        help='repeat the real core installer without recompiling the renderer')
    args = parser.parse_args()
    if os.geteuid() == 0 or os.environ.get('XDG_RUNTIME_DIR') != '/run/user/1000':
        raise SystemExit('Run as the integration VM desktop user with its real user manager')
    project = Path(__file__).resolve().parents[2]
    command = ['build.sh', '--install', '--core-only'] if args.core_only else ['install.sh']
    budget_minutes = 5 if args.core_only else 65
    child = pexpect.spawn('/usr/bin/bash', command, cwd=str(project),
                          encoding='utf-8', codec_errors='replace', timeout=60,
                          dimensions=(40, 160))
    child.logfile_read = sys.stdout
    deadline = time.monotonic() + budget_minutes * 60
    prompts = [
        r':: Proceed with installation\? \[Y/n\]\s*',
        r'Enter a number \(default=1\):\s*',
        r'Build and install (?:harletty-bridge|mpv-omniphony|sony-tracker) from the recipe above\? \[Y/n\]\s*',
        # Detect other common confirmation prompts, but never approve them.
        r'[^\r\n]*\? \[[yYnN]/[yYnN]\]\s*',
        r'\[sudo\] password for [^:]+:\s*',
        pexpect.EOF,
        pexpect.TIMEOUT,
    ]
    try:
        while time.monotonic() < deadline:
            matched = child.expect(prompts)
            if matched < 3:
                print('\n[CI] Accepting the displayed installer/package default.', flush=True)
                child.sendline('')
            elif matched in (3, 4):
                raise RuntimeError('Unexpected interactive prompt: ' + str(child.after))
            elif matched == 5:
                child.close()
                return child.exitstatus if child.exitstatus is not None else 128 + (child.signalstatus or 1)
        raise TimeoutError(f'The real installer exceeded its {budget_minutes} minute test budget')
    finally:
        if child.isalive():
            child.terminate(force=True)


if __name__ == '__main__':
    sys.exit(main())
