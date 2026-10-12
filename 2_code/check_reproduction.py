"""
Check that a fresh run reproduces the committed results.

Compares every csv in an output folder with a reference copy of the same file, column by
column, and reports the largest difference. Floating point is not bit-for-bit reproducible
across machines and library builds, so the verdict is whether the numbers agree at the four
decimal places the tables print, and whether any significance star would change.

    python check_reproduction.py                      against output/ as committed at HEAD
    python check_reproduction.py --ref ../old_output  against a folder you kept
    python check_reproduction.py --out /some/output --ref /some/reference

Exit status is 0 when every file agrees at four decimals, 1 otherwise.
"""
import argparse
import io
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DECIMALS = 4
CUTS = [0.01, 0.05, 0.1]      # the thresholds behind ***, ** and * in the tables


def stars(p):
    """The star a p value earns, as a string. A missing p keeps a label of its own, so that a
    coefficient that gains or loses its p value counts as a change rather than comparing NaN
    with NaN, which is never equal."""
    cut = pd.cut(p, [-np.inf] + CUTS + [np.inf], labels=["***", "**", "*", ""], right=False)
    return cut.astype(object).where(p.notna(), "no p value").astype(str)


def reference_from_git(repo, name):
    """The committed version of output/<name>, read without touching the working tree."""
    r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:output/{name}"],
                       capture_output=True)
    if r.returncode:
        return None
    return io.BytesIO(r.stdout)


def is_number(s):
    # a boolean column is numeric to pandas, but subtracting two of them is an error
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def compare(a, b):
    """Returns (verdict, detail). a is the reference, b the new run."""
    if a.shape != b.shape:
        return "SHAPE", f"{a.shape} vs {b.shape}"
    if list(a.columns) != list(b.columns):
        return "COLUMNS", "the column names or their order differ"

    num = [c for c in a.columns if is_number(a[c]) and is_number(b[c])]
    other = [c for c in a.columns if c not in num]

    for c in other:
        if not a[c].astype(str).equals(b[c].astype(str)):
            return "TEXT", f"column {c!r} differs"

    if not num:
        return "OK", "no numeric column; text identical"

    x, y = a[num], b[num]
    if not (x.isna() == y.isna()).all().all():
        return "MISSING", "a value is missing on one side only"

    gap = (x - y).abs().to_numpy()
    worst = float(np.nanmax(gap)) if np.isfinite(gap).any() else 0.0

    rounded = (x.round(DECIMALS) != y.round(DECIMALS)) & ~x.isna()
    n_cells = int(rounded.to_numpy().sum())

    n_stars = 0
    if "p" in a.columns and "p" in num:
        n_stars = int((stars(a["p"]) != stars(b["p"])).sum())

    if n_cells or n_stars:
        where = ", ".join(c for c in num if rounded[c].any()) or "-"
        return "DIFFERS", (f"max |diff| {worst:.3e}; {n_cells} cells differ at {DECIMALS} dp "
                           f"({where}); {n_stars} stars change")
    return "OK", f"max |diff| {worst:.3e}; identical at {DECIMALS} dp, stars unchanged"


def main():
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=here / "output", help="the folder a run wrote")
    ap.add_argument("--ref", type=Path, default=None,
                    help="a reference folder; the default is output/ as committed at HEAD")
    args = ap.parse_args()

    new = sorted(p.name for p in args.out.glob("*.csv"))
    if not new:
        sys.exit(f"no csv in {args.out}; has the analysis been run?")

    rows, failed, missing = [], 0, 0
    for name in new:
        ref = (io.BytesIO(( args.ref / name).read_bytes()) if args.ref
               else reference_from_git(here, name))
        if ref is None or (args.ref and not (args.ref / name).exists()):
            rows.append((name, "NO REF", "nothing to compare against"))
            missing += 1
            continue
        verdict, detail = compare(pd.read_csv(ref), pd.read_csv(args.out / name))
        rows.append((name, verdict, detail))
        failed += verdict not in ("OK", "NO REF")

    width = max(len(n) for n, _, _ in rows)
    source = args.ref if args.ref else "output/ at HEAD"
    print(f"comparing {args.out} with {source}\n")
    for name, verdict, detail in rows:
        print(f"  {name:{width}}  {verdict:8}  {detail}")

    print(f"\n{len(rows)} files: {len(rows) - failed - missing} reproduced, {failed} differ, "
          f"{missing} without a reference")
    if failed:
        print("the run does NOT reproduce the reference")
    else:
        print(f"the run reproduces the reference to {DECIMALS} decimal places")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
