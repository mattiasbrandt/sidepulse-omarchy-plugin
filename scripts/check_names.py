#!/usr/bin/env python3
"""Fail on misspellings of the SidePulse name in tracked text files (D24).

Allowed: "SidePulse" (the name), all-lowercase "sidepulse" (identifiers:
package, CLI, service, paths, repo slug, plugin id) and "SIDEPULSE_" (an
environment-variable prefix). Anything else that spells side + pulse, in any
case or with a space, hyphen or underscore between them, is reported.
GLOSSARY.md and this script are skipped: they spell the wrong forms on purpose.

Usage: python3 -I scripts/check_names.py   (from anywhere inside the repo)
"""

from __future__ import annotations

import re
import subprocess
import sys

SKIP = {"GLOSSARY.md", "uv.lock", "scripts/check_names.py"}
PATTERN = re.compile(r"side[ _-]?pulse(_?)", re.IGNORECASE)


def allowed(match: re.Match[str]) -> bool:
    word = match.group(0)
    core = word.rstrip("_")
    return core == "SidePulse" or core == "sidepulse" or (core == "SIDEPULSE" and match.group(1) == "_")


def main() -> int:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          check=True).stdout.strip()
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, check=True,
                           cwd=root).stdout.split("\0")
    problems = []
    for path in filter(None, files):
        if path in SKIP:
            continue
        try:
            text = open(f"{root}/{path}", encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in PATTERN.finditer(line):
                if not allowed(match):
                    problems.append(f"{path}:{number}: '{match.group(0)}' (write SidePulse; see GLOSSARY.md)")
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"check_names.py: {len([f for f in files if f])} tracked files, names OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
