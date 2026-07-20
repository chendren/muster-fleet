#!/usr/bin/env python3
"""Computer orchestrator — route + personality."""

from __future__ import annotations

import json
import sys

from brain import route
from personality import greet


def execute(text: str) -> dict:
    return route(text)


def greet_captain() -> dict:
    return greet()


def main():
    if len(sys.argv) < 2:
        print('Usage: computer.py "<command>" | computer.py --greet', file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] in ("--greet", "greet"):
        print(json.dumps(greet_captain(), indent=2))
        return
    text = " ".join(sys.argv[1:])
    print(json.dumps(execute(text), indent=2))


if __name__ == "__main__":
    main()
