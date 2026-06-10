"""Compare CLI and editor-addon scan results against the parity golden file.

Both implementations must report the same (severity, code, file) multiset as
``expected.json``. CLI⇔addon equality then follows transitively.

Normalisation applied to every issue before comparison:

* ``file``: ``None`` → ``""``, backslashes → ``/``, leading ``res://`` stripped.
* ``DUPLICATE_UID``: ``file`` forced to ``""`` — the CLI reports no location
  (the claimants are in ``details``) while the addon points at the first
  claimant so the editor can open it. Both are correct for their medium.

Usage (CI)::

    python tests/parity/compare.py \
        --cli /tmp/cli_report.json \
        --addon /tmp/addon_out.txt \
        --expected tests/parity/expected.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ADDON_MARKER = "PARITY_JSON:"

Key = tuple[str, str, str]


def normalize(severity: str, code: str, file: str | None) -> Key:
    f = (file or "").replace("\\", "/")
    if f.startswith("res://"):
        f = f[len("res://") :]
    if code == "DUPLICATE_UID":
        f = ""
    return (severity, code, f)


def load_expected(path: Path) -> Counter[Key]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    return Counter(normalize(e["severity"], e["code"], e["file"]) for e in entries)


def load_cli(path: Path) -> Counter[Key]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return Counter(normalize(i["severity"], i["code"], i.get("file")) for i in report["issues"])


def load_addon(path: Path) -> Counter[Key]:
    """Extract the PARITY_JSON line from the Godot headless stdout dump."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(ADDON_MARKER):
            issues = json.loads(line[len(ADDON_MARKER) :])
            return Counter(normalize(i["severity"], i["code"], i.get("file")) for i in issues)
    raise SystemExit(
        f"error: no line starting with {ADDON_MARKER!r} in {path} — "
        "the addon runner did not produce output (see the Godot log above)."
    )


def diff(name: str, got: Counter[Key], want: Counter[Key]) -> bool:
    """Print a readable diff; return True when *got* matches *want*."""
    missing = want - got
    extra = got - want
    if not missing and not extra:
        print(f"OK   {name}: {sum(got.values())} issues match expected.json")
        return True
    print(f"FAIL {name}:")
    for key, n in sorted(missing.items()):
        print(f"  missing {n}x  {key[0]:8} {key[1]:26} {key[2]}")
    for key, n in sorted(extra.items()):
        print(f"  extra   {n}x  {key[0]:8} {key[1]:26} {key[2]}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cli", type=Path, required=True, help="gdoctor --format json output file")
    ap.add_argument("--addon", type=Path, required=True, help="Godot headless stdout dump")
    ap.add_argument("--expected", type=Path, required=True, help="expected.json golden file")
    args = ap.parse_args()

    want = load_expected(args.expected)
    ok = diff("CLI  ", load_cli(args.cli), want)
    ok = diff("addon", load_addon(args.addon), want) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
