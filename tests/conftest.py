"""Shared fixtures and test data factories for ezgb tests.

Creates real git objects in temporary bare repos using pygit2 so
the BugReader can read them without subprocess mocking.
"""

from __future__ import annotations

import json
from pathlib import Path

import pygit2
import pytest
from typing_extensions import override

from ezgb._reader import BugReader
from ezgb._types import JsonObject
from ezgb._writer import BugWriter

# -- Constants ---------------------------------------------------------------

IDENTITY_ID = 'a' * 64
IDENTITY_ID_2 = 'b' * 64
BUG_ID = 'c' * 64

# Signature used for all test commits
_SIG = pygit2.Signature('Test', 'test@test.com')

CliResult = tuple[int, str, str]


# -- Test data factories -----------------------------------------------------


def make_identity_version(name: str, email: str) -> str:
    """Build a JSON identity version blob (format 2)."""
    return json.dumps(
        {
            'version': 2,
            'unix_time': 1700000000,
            'name': name,
            'email': email,
            'login': email.split('@')[0],
        }
    )


def make_op_pack(author_id: str, ops: list[JsonObject]) -> str:
    """Build a JSON operation pack with author and ops array."""
    return json.dumps(
        {
            'author': {'id': author_id},
            'ops': ops,
        }
    )


def make_create_op(title: str, message: str, timestamp: int = 1700000000) -> JsonObject:
    """Build an OP_CREATE (type 1) operation."""
    return {
        'type': 1,
        'timestamp': timestamp,
        'title': title,
        'message': message,
        'files': [],
    }


def make_comment_op(message: str, timestamp: int = 1700001000) -> JsonObject:
    """Build an OP_ADD_COMMENT (type 3) operation."""
    return {
        'type': 3,
        'timestamp': timestamp,
        'message': message,
        'files': [],
    }


def make_set_title_op(title: str, timestamp: int = 1700002000) -> JsonObject:
    """Build an OP_SET_TITLE (type 2) operation."""
    return {'type': 2, 'timestamp': timestamp, 'title': title}


def make_set_status_op(status: int, timestamp: int = 1700003000) -> JsonObject:
    """Build an OP_SET_STATUS (type 4) operation."""
    return {'type': 4, 'timestamp': timestamp, 'status': status}


def make_edit_comment_op(
    target: str, message: str, timestamp: int = 1700002500
) -> JsonObject:
    """Build an OP_EDIT_COMMENT (type 6) operation."""
    return {
        'type': 6,
        'timestamp': timestamp,
        'target': target,
        'message': message,
    }


def make_label_change_op(
    added: list[str] | None = None,
    removed: list[str] | None = None,
    timestamp: int = 1700004000,
) -> JsonObject:
    """Build an OP_LABEL_CHANGE (type 5) operation."""
    return {
        'type': 5,
        'timestamp': timestamp,
        # git-bug serializes the unused side of a label change as null.
        'added': list(added) if added is not None else None,
        'removed': list(removed) if removed is not None else None,
    }


def make_set_metadata_op(
    metadata: JsonObject, timestamp: int = 1700005000
) -> JsonObject:
    """Build an OP_SET_METADATA (type 8) operation."""
    return {'type': 8, 'timestamp': timestamp, 'new_metadata': metadata}


def make_noop_op(timestamp: int = 1700006000) -> JsonObject:
    """Build an OP_NOOP (type 7) operation."""
    return {'type': 7, 'timestamp': timestamp}


# -- Real git object helpers -------------------------------------------------


def _create_bug_commit(
    repo: pygit2.Repository,
    refname: str,
    ops_json: str,
    parent_oid: pygit2.Oid | None = None,
    version_tag: str = 'version-4',
) -> pygit2.Oid:
    """Create a single bug commit with an ops blob and version marker.

    If *parent_oid* is given the new commit is chained after it.
    Returns the new commit OID.
    """
    ops_blob = repo.create_blob(ops_json.encode())
    version_blob = repo.create_blob(b'')

    tb = repo.TreeBuilder()
    tb.insert('ops', ops_blob, pygit2.GIT_FILEMODE_BLOB)
    tb.insert(version_tag, version_blob, pygit2.GIT_FILEMODE_BLOB)
    tree_oid = tb.write()

    parents = [parent_oid] if parent_oid else []
    return repo.create_commit(
        refname,
        _SIG,
        _SIG,
        'op pack',
        tree_oid,
        parents,
    )


def _create_identity_commit(
    repo: pygit2.Repository, refname: str, version_json: str
) -> pygit2.Oid:
    """Create an identity commit with a ``version`` blob.

    Returns the commit OID.
    """
    version_blob = repo.create_blob(version_json.encode())

    tb = repo.TreeBuilder()
    tb.insert('version', version_blob, pygit2.GIT_FILEMODE_BLOB)
    tree_oid = tb.write()

    return repo.create_commit(
        refname,
        _SIG,
        _SIG,
        'identity',
        tree_oid,
        [],
    )


# -- Convenience setup -------------------------------------------------------


def setup_identity(repo_path: str, identity_id: str, name: str, email: str) -> None:
    """Create real identity git objects in the repo at *repo_path*.

    Writes a ``refs/identities/<id>`` ref pointing at a commit whose
    tree contains a ``version`` blob with the identity JSON.
    """
    repo = pygit2.Repository(str(repo_path))
    version_json = make_identity_version(name, email)
    refname = 'refs/identities/%s' % identity_id
    _create_identity_commit(repo, refname, version_json)


def setup_single_bug(
    repo_path: str,
    reader: BugReader,
    bid: str = BUG_ID,
    title: str = 'Test bug',
    message: str = 'Bug description',
    timestamp: int = 1700000000,
    extra_ops: list[JsonObject] | None = None,
    author_id: str = IDENTITY_ID,
    extra_packs: list[str] | None = None,
) -> None:
    """Create real git objects for a single bug with one create op.

    The bug ref ``refs/bugs/<bid>`` is created in the repo at
    *repo_path*. The identity for *author_id* is also created.

    If *extra_packs* is given, each element is a JSON string that
    becomes an additional chained commit (for multi-pack tests).
    """
    repo = pygit2.Repository(str(repo_path))
    refname = 'refs/bugs/%s' % bid

    ops = [make_create_op(title, message, timestamp)]
    if extra_ops:
        ops.extend(extra_ops)
    pack_json = make_op_pack(author_id, ops)

    parent = _create_bug_commit(repo, refname, pack_json)

    if extra_packs:
        for extra_json in extra_packs:
            parent = _create_bug_commit(
                repo,
                refname,
                extra_json,
                parent_oid=parent,
            )

    # Pre-cache bug ID resolution so tests don't need separate
    # ref enumeration setup.
    reader._resolve_cache[bid] = bid

    # Identity resolution
    setup_identity(repo_path, author_id, 'Alice', 'alice@example.com')


# -- Fixtures ----------------------------------------------------------------


class RecordingBugWriter(BugWriter):
    """BugWriter test double that records CLI calls and canned responses."""

    def __init__(self, repo_path: str, reader: BugReader) -> None:
        super().__init__(repo_path, reader)
        self._cli_calls: list[list[str]] = []
        self._cli_routes: dict[str, CliResult] = {}

    @override
    def _cli(
        self,
        args: list[str],
        stdin: str | None = None,
    ) -> CliResult:
        self._cli_calls.append(args)
        joined = ' '.join(args)
        for key, value in self._cli_routes.items():
            if key in joined:
                return value
        return (0, '', '')


@pytest.fixture()
def repo_path(tmp_path: Path) -> str:
    """Create a bare git repo and return its path as a string."""
    repo_dir = tmp_path / 'repo'
    pygit2.init_repository(str(repo_dir), bare=True)
    return str(repo_dir)


@pytest.fixture()
def reader(repo_path: str) -> BugReader:
    """Create a BugReader pointing at the test repo."""
    return BugReader(repo_path)


@pytest.fixture()
def writer(reader: BugReader, repo_path: str) -> RecordingBugWriter:
    """Create a BugWriter with git_bug_cli mocked.

    Write operations shell out to the ``git bug`` CLI, which isn't
    available in tests, so we mock it. The mock captures calls in
    ``writer._cli_calls`` for assertion.
    """
    return RecordingBugWriter(repo_path, reader)
