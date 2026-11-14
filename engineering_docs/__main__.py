import argparse
from pathlib import Path

from .catalog import import_snapshot, initialize


def run():
    parser = argparse.ArgumentParser(
        description="Import a reconciled Engineering Documents snapshot"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--sha256", required=True, help="Expected hash from the approved source export"
    )
    args = parser.parse_args()
    initialize(args.database)
    print(import_snapshot(args.database, args.manifest, args.sha256))


if __name__ == "__main__":
    run()
