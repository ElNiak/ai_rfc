"""Structured commit history for an implementation under test.

Extracts a repository into a deterministic JSONL corpus plus a derived SQLite
index. Makes no model calls and opens no sockets.
"""

from .git_log import (
    DEFAULT_FILE_CAP,
    GitError,
    ShallowRepositoryError,
    extract,
    extract_commits,
    read_file_changes,
)
from .index import INDEX_FILE, StaleIndexError, build_index, open_index
from .models import Commit, ExtractionReport, FileChange
from .store import COMMITS_FILE, FILES_FILE, REPORT_FILE, read_corpus, write_corpus

__all__ = [
    "COMMITS_FILE",
    "DEFAULT_FILE_CAP",
    "FILES_FILE",
    "INDEX_FILE",
    "REPORT_FILE",
    "Commit",
    "ExtractionReport",
    "FileChange",
    "GitError",
    "ShallowRepositoryError",
    "StaleIndexError",
    "build_index",
    "extract",
    "open_index",
    "extract_commits",
    "read_corpus",
    "read_file_changes",
    "write_corpus",
]
