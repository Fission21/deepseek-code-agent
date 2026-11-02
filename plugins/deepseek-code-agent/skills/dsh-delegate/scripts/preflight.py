"""Offline DSH installation diagnostics; no model session, model call, or report write."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from paths import preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsh', help='DSH CLI path; default discovers DSH_BIN, PATH, or the macOS app install')
    parser.add_argument('--huashu-entry', help='Optional installed huashu-chrome src/cli.js path')
    args = parser.parse_args(argv)
    report = preflight(args.dsh, args.huashu_entry)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['preflight'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
