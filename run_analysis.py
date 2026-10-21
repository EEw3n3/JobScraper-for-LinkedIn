import sys

from main import cli


if __name__ == "__main__":
    raise SystemExit(cli(["analyze", *sys.argv[1:]]))
