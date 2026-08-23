#!/usr/bin/env python3
"""Profile a CSV using the standard library only.

Deliberately dependency-free: it runs in any sandbox image, including ones
without pandas, and it costs no tokens because scripts are run rather than read
into context.

    python3 profile_csv.py data.csv
    python3 profile_csv.py data.csv --sample 5

Prints a per-column table (inferred type, nulls, distinct, numeric stats) and,
with --sample, the first N rows.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

NULLISH = {"", "na", "n/a", "null", "none", "nan", "-"}
BOOLISH = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d")


def is_null(value: str) -> bool:
    return value.strip().lower() in NULLISH


def as_number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def as_date(value: str) -> date | None:
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def infer_type(values: list[str]) -> str:
    """Infer a column type from its non-null values. Unanimity or `string`."""
    if not values:
        return "empty"
    lowered = [v.strip().lower() for v in values]
    if all(v in BOOLISH for v in lowered) and len(set(lowered)) <= 2:
        return "boolean"
    numbers = [as_number(v) for v in values]
    if all(n is not None for n in numbers):
        return "integer" if all(float(n).is_integer() for n in numbers if n is not None) else "float"
    if all(as_date(v) is not None for v in values):
        return "date"
    return "string"


def profile(path: Path, sample: int) -> int:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            print(f"{path}: file is empty")
            return 1
        rows = list(reader)

    print(f"file    : {path}")
    print(f"rows    : {len(rows)}")
    print(f"columns : {len(header)}")
    ragged = [i for i, row in enumerate(rows, start=2) if len(row) != len(header)]
    if ragged:
        head = ", ".join(str(n) for n in ragged[:5])
        print(f"WARNING : {len(ragged)} row(s) do not match the header width (first at line {head})")
    print()

    widths = max((len(h) for h in header), default=6)
    print(f"{'column'.ljust(widths)}  {'type':<8} {'nulls':>12} {'distinct':>9}  stats")
    print("-" * (widths + 45))

    for idx, name in enumerate(header):
        raw = [row[idx] if idx < len(row) else "" for row in rows]
        present = [v for v in raw if not is_null(v)]
        nulls = len(raw) - len(present)
        pct = (nulls / len(raw) * 100) if raw else 0.0
        kind = infer_type(present)
        distinct = len(Counter(present))

        stats = ""
        if kind in ("integer", "float"):
            numbers = [n for n in (as_number(v) for v in present) if n is not None]
            if numbers:
                stats = (
                    f"min={min(numbers):g} max={max(numbers):g} "
                    f"mean={statistics.fmean(numbers):.4g} median={statistics.median(numbers):g}"
                )
        elif kind == "date":
            dates = [d for d in (as_date(v) for v in present) if d is not None]
            if dates:
                stats = f"min={min(dates)} max={max(dates)}"

        print(f"{name.ljust(widths)}  {kind:<8} {f'{nulls} ({pct:.1f}%)':>12} {distinct:>9}  {stats}")

    if sample > 0 and rows:
        print(f"\nfirst {min(sample, len(rows))} row(s):")
        print(",".join(header))
        for row in rows[:sample]:
            print(",".join(row))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Profile a CSV with the standard library only.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--sample", type=int, default=0, help="also print the first N rows")
    args = parser.parse_args(argv)
    if not args.path.is_file():
        print(f"{args.path}: not a file", file=sys.stderr)
        return 2
    return profile(args.path, args.sample)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
