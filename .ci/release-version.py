#!/usr/bin/env python3
"""Read a release version from the selected machine's MACHINE-X.Y.Z tag."""

import argparse
import re
import sys


def release_version(machine, tag):
    if not isinstance(machine, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._+-]*", machine):
        raise ValueError("MACHINE must be a valid board name")
    if not isinstance(tag, str) or not tag.startswith(machine + "-"):
        raise ValueError("Release tag must belong to the selected MACHINE")
    version = tag[len(machine) + 1:]
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("Release tag must be MACHINE-X.Y.Z with numeric version fields")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        print(release_version(args.machine, args.tag))
    except ValueError as error:
        print("Release version error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
