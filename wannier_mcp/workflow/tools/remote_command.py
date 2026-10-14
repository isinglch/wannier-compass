#!/usr/bin/env python3
"""SSH transport with bounded read retries and explicit uncertain mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from workflow_runtime import atomic_json, now, sha, runtime_directory

TRANSPORT_MESSAGES = ('banner exchange', 'Connection closed', 'Connection refused',
                      'Connection timed out', 'Operation timed out', 'Broken pipe',
                      'Could not resolve hostname', 'Network is unreachable')


def remote(host, command, workspace, *, read_only=False, retries=2, timeout=120):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@:-]*', host):
        raise ValueError('Use one configured SSH host, not shell or option text.')
    if not command or any(not isinstance(x, str) for x in command):
        raise ValueError('Command must be a nonempty argument array.')
    if not 0 <= retries <= 3 or timeout <= 0:
        raise ValueError('At most three bounded read retries are supported.')
    sockets = Path(tempfile.gettempdir()) / ('wan-ssh-' + str(os.getuid()))
    sockets.mkdir(mode=0o700, exist_ok=True)
    if sockets.is_symlink() or sockets.stat().st_uid != os.getuid() or sockets.stat().st_mode & 0o077:
        raise ValueError('SSH control socket directory must be private and owned by this user.')
    socket = sockets / hashlib.sha256(host.encode()).hexdigest()[:12]
    argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'ControlMaster=auto', '-o', 'ControlPersist=600',
            '-o', 'ControlPath=' + str(socket), host, shlex.join(command)]
    attempts = []
    for index in range(1 + (retries if read_only else 0)):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
            code, stdout, stderr = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as error:
            decode = lambda x: x.decode(errors='replace') if isinstance(x, bytes) else (x or '')
            code, stdout, stderr = None, decode(error.stdout), decode(error.stderr)
        except OSError as error:
            code, stdout, stderr = None, '', str(error)
        attempts.append({'attempt': index + 1, 'exit_code': code, 'stdout': stdout,
                         'stderr': stderr, 'finished_utc': now()})
        transport_failure = code is None or (code == 255 and any(s in stderr for s in TRANSPORT_MESSAGES))
        if not (read_only and transport_failure and index < retries):
            break
        time.sleep(min(2 ** index, 4))
    import uuid
    path = runtime_directory(workspace, 'remote') / (uuid.uuid4().hex + '.json')
    atomic_json(path, {'host': host, 'command': command, 'read_only': read_only, 'attempts': attempts})
    uncertain = not read_only and (code is None or code == 255)
    return {'ok': code == 0, 'exit_code': code,
            'status': 'completion_unknown' if uncertain else 'completed' if code == 0 else 'remote_command_failed',
            'attempts': len(attempts), 'automatic_mutation_retry': False,
            'log': str(path), 'log_sha256': sha(path),
            'stdout_excerpt': stdout[-1500:], 'stderr_excerpt': stderr[-1500:],
            'next_action': 'reconcile remote operation state before any resubmission' if uncertain else
                           'inspect remote CLI error; do not treat a scientific gate rejection as an SSH outage' if code else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--read-only', action='store_true', help='Declare a side-effect-free query; only these may retry.')
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    result = remote(args.host, command, args.workspace, read_only=args.read_only,
                    retries=args.retries, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result['exit_code'] if result['exit_code'] is not None else 124)


if __name__ == '__main__':
    main()
