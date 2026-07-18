#!/usr/bin/env python3

import argparse

from commands.doctor import doctor
from commands.version import version


def main():

    parser = argparse.ArgumentParser(
        prog="nuttz",
        description="NUTTZ OS Command Line Interface"
    )

    parser.add_argument(
        "command",
        nargs="?",
        default="help"
    )

    args = parser.parse_args()

    if args.command == "doctor":
        doctor()

    elif args.command == "version":
        version()

    else:
        print()
        print("NUTTZ OS")
        print()
        print("Commands")
        print("----------------")
        print("doctor")
        print("version")
        print()


if __name__ == "__main__":
    main()
