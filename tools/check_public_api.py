from __future__ import annotations

import json
import sys
from pathlib import Path

import turbo


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/check_public_api.py <baseline-json>")
    baseline_path = Path(sys.argv[1])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = sorted(str(x) for x in baseline)
    current = sorted(str(x) for x in turbo.__all__)
    if current != expected:
        missing = sorted(set(expected) - set(current))
        added = sorted(set(current) - set(expected))
        print("[api-compat] public API drift detected")
        if missing:
            print("missing symbols:", ", ".join(missing))
        if added:
            print("added symbols:", ", ".join(added))
        raise SystemExit(1)
    print("[api-compat] public API matches baseline")


if __name__ == "__main__":
    main()
