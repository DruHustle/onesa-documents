"""Document ownership, exact revision lineage and object permissions."""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY, product TEXT NOT NULL, discipline TEXT NOT NULL, author TEXT NOT NULL,
 bookmark TEXT NOT NULL UNIQUE, current TEXT NOT NULL,
 FOREIGN KEY(id,current) REFERENCES revisions(document,id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS revisions(
 document TEXT REFERENCES documents, id TEXT, author TEXT NOT NULL, created TEXT NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL, approved INTEGER NOT NULL,
 PRIMARY KEY(document,id));
CREATE TABLE IF NOT EXISTS grants(
 document TEXT REFERENCES documents, principal TEXT, permission TEXT CHECK(permission IN ('read','edit')),
 PRIMARY KEY(document,principal));
CREATE TABLE IF NOT EXISTS objects(digest TEXT PRIMARY KEY, bytes BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(
 id TEXT PRIMARY KEY, document TEXT NOT NULL, revision TEXT NOT NULL, name TEXT NOT NULL,
 digest TEXT NOT NULL REFERENCES objects, FOREIGN KEY(document,revision) REFERENCES revisions);
CREATE TABLE IF NOT EXISTS imports(digest TEXT PRIMARY KEY);
"""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,255}", value
    ):
        raise ValueError("Invalid source ID")
    return value


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    with connection(path) as db:
        db.executescript(SCHEMA)
        # A pre-existing database was written by the engineering-only edition.
        with db:
            db.execute("BEGIN IMMEDIATE")
            if "collection" not in {
                r["name"] for r in db.execute("PRAGMA table_info(documents)")
            }:
                db.execute(
                    "ALTER TABLE documents ADD COLUMN collection TEXT NOT NULL DEFAULT 'engineering'"
                )


def import_snapshot(path, manifest, digest):
    """Import one externally reconciled snapshot of new documents. A repeat is a no-op."""
    base = Path(manifest).resolve().parent
    data = Path(manifest).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Manifest checksum mismatch")
    batch = json.loads(data)
    if batch["schema"] != 1:
        raise ValueError("Unsupported document manifest")
    totals = {"documents": 0, "revisions": 0, "attachments": 0, "grants": 0}
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM imports WHERE digest=?", (digest,)).fetchone():
            return {"already_imported": True}
        for doc in batch["documents"]:
            did, current = identifier(doc["id"]), identifier(doc["current"])
            bookmark = doc["bookmark"]
            if (
                not isinstance(bookmark, str)
                or len(bookmark) > 2048
                or not re.fullmatch(r"/docs/[A-Za-z0-9_~./-]+", bookmark)
                or any(part in (".", "..", "") for part in bookmark.split("/")[2:])
            ):
                raise ValueError("Unsupported legacy bookmark")
            collection = doc.get("collection", "engineering")
            if not isinstance(collection, str) or not re.fullmatch(
                r"[a-z][a-z0-9-]{0,63}", collection
            ):
                raise ValueError("Invalid internal collection")
            db.execute(
                "INSERT INTO documents (id,product,discipline,author,bookmark,current,collection) VALUES(?,?,?,?,?,?,?)",
                (
                    did,
                    doc["product"],
                    doc["discipline"],
                    doc["author"],
                    bookmark,
                    current,
                    collection,
                ),
            )
            totals["documents"] += 1
            for rev in doc["revisions"]:
                rid = identifier(rev["id"])
                if not isinstance(rev["approved"], bool) or (
                    rid == current and not rev["approved"]
                ):
                    raise ValueError(
                        "Current published revision must have approval evidence"
                    )
                if rev["source_url"]:
                    u = urlsplit(rev["source_url"])
                    if (
                        u.scheme != "https"
                        or not u.hostname
                        or u.username
                        or u.password
                    ):
                        raise ValueError("Invalid provenance URL")
                    if u.hostname == "github.com" and not re.search(
                        "/(?:blob|tree|commit)/[a-f0-9]{40}(?:/|$)", u.path
                    ):
                        raise ValueError("Git provenance must name an immutable commit")
                db.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        did,
                        rid,
                        rev["author"],
                        rev["created"],
                        rev["title"],
                        rev["body"],
                        rev["source_url"],
                        rev["approved"],
                    ),
                )
                totals["revisions"] += 1
                for attachment in rev["attachments"]:
                    sha = attachment["sha256"]
                    if not re.fullmatch("[a-f0-9]{64}", sha):
                        raise ValueError("Invalid attachment hash")
                    obj = base / "objects" / sha
                    if obj.resolve() != obj or not obj.is_file():
                        raise ValueError("Invalid attachment location")
                    content = obj.read_bytes()
                    if hashlib.sha256(content).hexdigest() != sha:
                        raise ValueError("Attachment checksum mismatch")
                    existing = db.execute(
                        "SELECT bytes FROM objects WHERE digest=?", (sha,)
                    ).fetchone()
                    if existing is not None:
                        if existing["bytes"] != content:
                            raise ValueError("Existing attachment object checksum mismatch")
                    else:
                        db.execute(
                            "INSERT INTO objects VALUES(?,?)", (sha, content)
                        )
                    db.execute(
                        "INSERT INTO attachments VALUES(?,?,?,?,?)",
                        (
                            identifier(attachment["id"]),
                            did,
                            rid,
                            attachment["name"],
                            sha,
                        ),
                    )
                    totals["attachments"] += 1
            for grant in doc["grants"]:
                db.execute(
                    "INSERT INTO grants VALUES(?,?,?)",
                    (did, str(UUID(grant["user_id"])), grant["permission"]),
                )
                totals["grants"] += 1
        if totals != batch["expected_counts"]:
            raise ValueError("Inventory totals differ from the source reconciliation")
        db.execute("INSERT INTO imports VALUES(?)", (digest,))
    return totals


JOIN = """ FROM documents d JOIN grants g ON g.document=d.id
JOIN revisions r ON r.document=d.id AND r.id=d.current AND r.approved=1 """
FIELDS = "d.id,d.collection,d.product,d.discipline,d.bookmark,d.author,r.title,r.id AS revision,g.permission"


def search(db, uid, query, engineering_only=False):
    return [
        dict(row)
        for row in db.execute(
            "SELECT "
            + FIELDS
            + JOIN
            + "WHERE g.principal=? AND (?=0 OR d.collection='engineering') AND instr(lower(d.collection || char(10) || r.title || char(10) || d.product || char(10) || d.discipline),lower(?))>0 ORDER BY d.product,r.title,d.id",
            (uid, engineering_only, query),
        )
    ]


def document(db, uid, key, bookmark=False, engineering_only=False):
    field = "d.bookmark" if bookmark else "d.id"
    row = db.execute(
        "SELECT "
        + FIELDS
        + ",r.body,r.created,r.source,r.author AS revision_author"
        + JOIN
        + "WHERE g.principal=? AND (?=0 OR d.collection='engineering') AND "
        + field
        + "=?",
        (uid, engineering_only, key),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["attachments"] = [
        dict(row)
        for row in db.execute(
            "SELECT id,name,digest FROM attachments WHERE document=? AND revision=? ORDER BY id",
            (result["id"], result["revision"]),
        )
    ]
    return result


def attachment(db, uid, key, engineering_only=False):
    return db.execute(
        """SELECT a.name,o.bytes FROM attachments a JOIN objects o ON a.digest=o.digest
        JOIN revisions r ON a.document=r.document AND a.revision=r.id AND r.approved=1
        JOIN documents d ON d.id=a.document AND d.current=a.revision JOIN grants g ON g.document=a.document WHERE g.principal=? AND (?=0 OR d.collection='engineering') AND a.id=?""",
        (uid, engineering_only, key),
    ).fetchone()
