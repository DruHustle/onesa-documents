import argparse
import os
from pathlib import Path

from .catalog import import_snapshot, initialize


def run():
    parser = argparse.ArgumentParser(
        description="Import a reconciled Internal Documents snapshot"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--sha256", required=True, help="Expected hash from the approved source export"
    )
    args = parser.parse_args()
    if args.database.is_symlink():
        raise RuntimeError("Private Documents database must not be a symlink")
    database = args.database.resolve()
    directory = database.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.manifest.is_symlink():
        raise RuntimeError("Private Documents manifest must not be a symlink")
    manifest = args.manifest.resolve()
    if not manifest.is_file() or not manifest.is_relative_to(directory):
        raise RuntimeError("Private Documents manifest and objects must be inside DOCS_DATA")
    # The importer is a production entry point too: it must not create a
    # world-readable SQLite file before the web service has a chance to
    # enforce private storage permissions.
    try:
        os.chmod(directory, 0o700)
        if directory.stat().st_mode & 0o077:
            raise OSError("private import directory remains accessible to group/other")
    except OSError as exc:
        raise RuntimeError("Cannot secure private Documents import directory") from exc
    initialize(database)
    try:
        os.chmod(database, 0o600)
        if database.stat().st_mode & 0o077:
            raise OSError("private database remains accessible to group/other")
    except OSError as exc:
        raise RuntimeError("Cannot secure private Documents database") from exc
    print(import_snapshot(database, manifest, args.sha256))


if __name__ == "__main__":
    run()
