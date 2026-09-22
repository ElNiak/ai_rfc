"""A queryable index over the corpus.

Derived and disposable. The JSONL files are the durable record; this exists so
questions like "which commits touched this path" do not require a full scan.
It is **rebuilt, never migrated** — if its sources have moved on it refuses to
answer, because a stale index answers confidently from old data and nothing
about the answer looks wrong.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .store import COMMITS_FILE, FILES_FILE, read_corpus

INDEX_FILE = "index.sqlite"

_SCHEMA = """
CREATE TABLE commits (
    sha TEXT PRIMARY KEY,
    authored_at TEXT NOT NULL,
    author_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    is_merge INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    files_truncated INTEGER NOT NULL
);
CREATE TABLE file_changes (
    sha TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    previous_path TEXT
);
CREATE INDEX file_changes_path ON file_changes (path);
CREATE TABLE corpus_source (
    name TEXT PRIMARY KEY,
    digest TEXT NOT NULL
);
"""


class StaleIndexError(RuntimeError):
    """Raised when an index no longer matches the corpus it was built from."""


class IndexBuildError(RuntimeError):
    """Raised when the index cannot be written, though its corpus reads fine.

    Its own type rather than an ``OSError``, because it is not one:
    :class:`sqlite3.Error` derives from ``Exception``, and re-raising it as
    something it is not would make every handler that names ``OSError`` wrong
    about what it caught. The verbs that build an index catch this beside the
    errors they already name.
    """


def schema_columns() -> dict[str, tuple[str, ...]]:
    """Each table this index holds, with its columns in declaration order.

    **Derived from** :data:`_SCHEMA` **by sqlite itself**, not read off a list
    somebody kept in step by hand: the DDL is built here, into an in-memory
    database, and the answer comes back from ``sqlite_master`` and
    ``PRAGMA table_info``. So a column added to :data:`_SCHEMA` reaches every
    description of this index without a second edit, and none of them can
    describe a column the index does not have — which is the defect this
    exists to close (an arm guessed ``commit_sha``, ``parents`` and
    ``committed_at``, none of which are real).

    sqlite is used as the parser rather than a regular expression for the
    reason this package applies everywhere else: the grammar's own
    implementation is not an approximation of it.

    Returns:
        Table name to its column names, both in the order the schema declares
        them. Indexes are not tables and do not appear.
    """
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA)
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "ORDER BY rootpage"
            )
        ]
        return {
            name: tuple(
                str(column[1])
                for column in connection.execute(f'PRAGMA table_info("{name}")')
            )
            for name in names
        }
    finally:
        connection.close()


def schema_summary() -> str:
    """The index's tables and columns, as one line a description can carry.

    Args:
        None.

    Returns:
        ``table(col, col, …); table(col, …)`` over every table in
        :func:`schema_columns`.
    """
    return "; ".join(
        f"{table}({', '.join(columns)})" for table, columns in schema_columns().items()
    )


def _digest(path: Path) -> str:
    """Return a hex digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_index(directory: Path) -> Path:
    """Build the index from the corpus in ``directory``, replacing any existing one.

    Args:
        directory: A directory written by :func:`store.write_corpus`.

    Returns:
        Path to the index that was written.

    Raises:
        FileNotFoundError: If the corpus is absent.
        IndexBuildError: If sqlite cannot create, populate or close the index
            — a full disk, a read-only directory, a file that is not a
            database. The corpus itself is already on disk when this is
            raised, so the caller has a partial success to report.
    """
    commits, changes = read_corpus(directory)
    index_path = directory / INDEX_FILE
    index_path.unlink(missing_ok=True)

    # The connect is inside the clause, not above it: it is the call most
    # likely to fail (the file is created here) and it sat outside the only
    # `try` this function had. `sqlite3.Error` is the whole category — one
    # base class for every sqlite failure — rather than the three or four
    # subclasses somebody would otherwise list.
    try:
        conn = sqlite3.connect(index_path)
        try:
            conn.executescript(_SCHEMA)
            conn.executemany(
                "INSERT INTO commits VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.sha,
                        c.authored_at,
                        c.author_email,
                        c.subject,
                        int(c.is_merge),
                        c.file_count,
                        int(c.files_truncated),
                    )
                    for c in commits
                ],
            )
            conn.executemany(
                "INSERT INTO file_changes VALUES (?, ?, ?, ?)",
                [(c.sha, c.path, c.status, c.previous_path) for c in changes],
            )
            conn.executemany(
                "INSERT INTO corpus_source VALUES (?, ?)",
                [
                    (COMMITS_FILE, _digest(directory / COMMITS_FILE)),
                    (FILES_FILE, _digest(directory / FILES_FILE)),
                ],
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as error:
        raise IndexBuildError(f"{index_path}: {error}") from None
    return index_path


def open_index(directory: Path) -> sqlite3.Connection:
    """Open the index, refusing if its sources have changed since it was built.

    Args:
        directory: A directory containing a corpus and its index.

    Returns:
        An open connection. Its context-manager protocol commits or rolls back
        a transaction; it does not close the connection, so close it yourself.

    Raises:
        FileNotFoundError: If the index does not exist.
        StaleIndexError: If either JSONL file no longer matches the digest
            recorded when the index was built.
    """
    index_path = directory / INDEX_FILE
    if not index_path.exists():
        raise FileNotFoundError(f"no index in {directory}; run build_index first")

    conn = sqlite3.connect(index_path)
    recorded = dict(conn.execute("SELECT name, digest FROM corpus_source").fetchall())
    for name in (COMMITS_FILE, FILES_FILE):
        current = _digest(directory / name)
        if recorded.get(name) != current:
            conn.close()
            raise StaleIndexError(
                f"{name} has changed since the index was built; "
                f"rebuild it rather than trusting these answers"
            )
    return conn
