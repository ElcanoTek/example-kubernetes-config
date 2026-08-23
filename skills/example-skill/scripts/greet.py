#!/usr/bin/env python3
"""Trivial Level-3 demonstration script.

Exists to show the shape: standard library only, argument in, deterministic
output out, no network, no side effects outside stdout. Scripts are RUN rather
than read into context, so the model pays no tokens for this file.

    python3 skills/example-skill/scripts/greet.py "Rowan"
"""

import sys


def greet(name: str) -> str:
    name = name.strip() or "there"
    return f"Hello, {name}. This ran in the sandbox, not in the model."


def main(argv: list[str]) -> int:
    print(greet(argv[1] if len(argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
