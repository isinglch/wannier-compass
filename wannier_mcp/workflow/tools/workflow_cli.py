#!/usr/bin/env python3
"""Compact CLI entry to the unchanged bundled scientific tools."""
import argparse
import json
from pathlib import Path
import sys

from workflow_runtime import execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--full-output', action='store_true', help='Explicit opt-in; the complete record always stays on disk.')
    parser.add_argument('tool')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    result, parsed = execute(args.tool, arguments, args.workspace, timeout=args.timeout)
    if args.full_output:
        record = json.loads(Path(result['log']).read_text())
        print(record['stdout'], end='')
        print(record['stderr'], end='', file=sys.stderr)
    else:
        print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result['exit_code'] if result['exit_code'] is not None else 124)


if __name__ == '__main__':
    main()
