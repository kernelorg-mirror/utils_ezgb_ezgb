"""Shared fixtures and test data factories for ezgb tests.

Creates real git objects in temporary bare repos using pygit2 so
the BugReader can read them without subprocess mocking.
"""
import json
from unittest.mock import patch

import pygit2
import pytest

from ezgb._reader import BugReader
from ezgb._writer import BugWriter

# -- Constants ---------------------------------------------------------------

IDENTITY_ID = 'a' * 64
IDENTITY_ID_2 = 'b' * 64
BUG_ID = 'c' * 64

# Signature used for all test commits
_SIG = pygit2.Signature('Test', 'test@test.com')


# -- Test data factories -----------------------------------------------------

def make_identity_version(name, email):
    """Build a JSON identity version blob (format 2)."""
    return json.dumps({
        'version': 2,
        'unix_time': 1700000000,
        'name': name,
        'email': email,
        'login': email.split('@')[0],
    })


def make_op_pack(author_id, ops):
    """Build a JSON operation pack with author and ops array."""
    return json.dumps({
        'author': {'id': author_id},
        'ops': ops,
    })


def make_create_op(title, message, timestamp=1700000000):
    """Build an OP_CREATE (type 1) operation."""
    return {
        'type': 1,
        'timestamp': timestamp,
        'title': title,
        'message': message,
        'files': [],
    }


def make_comment_op(message, timestamp=1700001000):
    """Build an OP_ADD_COMMENT (type 3) operation."""
    return {
        'type': 3,
        'timestamp': timestamp,
        'message': message,
        'files': [],
    }


def make_set_title_op(title, timestamp=1700002000):
    """Build an OP_SET_TITLE (type 2) operation."""
    return {'type': 2, 'timestamp': timestamp, 'title': title}


def make_set_status_op(status, timestamp=1700003000):
    """Build an OP_SET_STATUS (type 4) operation."""
    return {'type': 4, 'timestamp': timestamp, 'status': status}


def make_edit_comment_op(target, message, timestamp=1700002500):
    """Build an OP_EDIT_COMMENT (type 6) operation."""
    return {
        'type': 6, 'timestamp': timestamp,
        'target': target, 'message': message,
    }


def make_label_change_op(added=None, removed=None, timestamp=1700004000):
    """Build an OP_LABEL_CHANGE (type 5) operation."""
    return {
        'type': 5, 'timestamp': timestamp,
        'added': added or [], 'removed': removed or [],
    }


def make_set_metadata_op(metadata, timestamp=1700005000):
    """Build an OP_SET_METADATA (type 8) operation."""
    return {'type': 8, 'timestamp': timestamp, 'new_metadata': metadata}


def make_noop_op(timestamp=1700006000):
    """Build an OP_NOOP (type 7) operation."""
    return {'type': 7, 'timestamp': timestamp}


# -- Real git object helpers -------------------------------------------------

def _create_bug_commit(repo, refname, ops_json, parent_oid=None,
                       version_tag='version-4'):
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
        refname, _SIG, _SIG, 'op pack', tree_oid, parents,
    )


def _create_identity_commit(repo, refname, version_json):
    """Create an identity commit with a ``version`` blob.

    Returns the commit OID.
    """
    version_blob = repo.create_blob(version_json.encode())

    tb = repo.TreeBuilder()
    tb.insert('version', version_blob, pygit2.GIT_FILEMODE_BLOB)
    tree_oid = tb.write()

    return repo.create_commit(
        refname, _SIG, _SIG, 'identity', tree_oid, [],
    )


# -- Convenience setup -------------------------------------------------------

def setup_identity(repo_path, identity_id, name, email):
    """Create real identity git objects in the repo at *repo_path*.

    Writes a ``refs/identities/<id>`` ref pointing at a commit whose
    tree contains a ``version`` blob with the identity JSON.
    """
    repo = pygit2.Repository(str(repo_path))
    version_json = make_identity_version(name, email)
    refname = 'refs/identities/%s' % identity_id
    _create_identity_commit(repo, refname, version_json)


def setup_single_bug(repo_path, reader, bid=BUG_ID, title='Test bug',
                     message='Bug description', timestamp=1700000000,
                     extra_ops=None, author_id=IDENTITY_ID,
                     extra_packs=None):
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
                repo, refname, extra_json, parent_oid=parent,
            )

    # Pre-cache bug ID resolution so tests don't need separate
    # ref enumeration setup.
    reader._resolve_cache[bid] = bid

    # Identity resolution
    setup_identity(repo_path, author_id, 'Alice', 'alice@example.com')


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture()
def repo_path(tmp_path):
    """Create a bare git repo and return its path as a string."""
    repo_dir = tmp_path / 'repo'
    pygit2.init_repository(str(repo_dir), bare=True)
    return str(repo_dir)


@pytest.fixture()
def reader(repo_path):
    """Create a BugReader pointing at the test repo."""
    return BugReader(repo_path)


@pytest.fixture()
def writer(reader, repo_path):
    """Create a BugWriter with git_bug_cli mocked.

    Write operations shell out to the ``git bug`` CLI, which isn't
    available in tests, so we mock it. The mock captures calls in
    ``writer._cli_calls`` for assertion.
    """
    cli_routes = {}
    cli_calls = []

    def _cli_side_effect(rp, args, stdin=None):
        cli_calls.append(args)
        joined = ' '.join(args)
        for key, value in cli_routes.items():
            if key in joined:
                if callable(value):
                    return value(rp, args)
                return value
        return (0, '', '')

    with patch('ezgb._writer.git_bug_cli',
               side_effect=_cli_side_effect), \
         patch('ezgb._git.git_bug_cli',
               side_effect=_cli_side_effect):
        w = BugWriter(repo_path, reader)
        w._cli_calls = cli_calls
        w._cli_routes = cli_routes
        yield w
