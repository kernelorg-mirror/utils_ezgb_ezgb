"""Shared fixtures and test data factories for ezgb tests.

Ported from bugspray's test_gitbug.py, adapted for ezgb's module
structure and dataclass-based API.
"""
import hashlib
import json
from unittest.mock import patch

import pytest

from ezgb._reader import BugReader
from ezgb._writer import BugWriter

# -- Constants ---------------------------------------------------------------

IDENTITY_ID = 'a' * 64
IDENTITY_ID_2 = 'b' * 64
BUG_ID = 'c' * 64
REPO_PATH = '/srv/git/test.git'


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


# -- Log output formatting ---------------------------------------------------

def format_log_raw(commit_hash, ops_blob):
    """Build output matching ``git log --raw --full-index --format=%H``."""
    return (
        '%s\n'
        ':100644 100644 %s %s A\tops\n'
        ':100644 100644 %s %s A\tversion-4\n'
        % (commit_hash, '0' * 40, ops_blob, '0' * 40, '0' * 40)
    )


def format_identity_log_raw(commit_hash, version_blob):
    """Build identity log output with a ``version`` entry."""
    return (
        '%s\n'
        ':100644 100644 %s %s A\tversion\n'
        % (commit_hash, '0' * 40, version_blob)
    )


# -- Convenience setup -------------------------------------------------------

def setup_identity(mock_git, identity_id, name, email):
    """Wire up mock routes for identity resolution.

    Derives unique hashes from *identity_id* so multiple identities
    can coexist without route collisions.
    """
    commit = hashlib.sha1(identity_id.encode()).hexdigest()
    version_blob = hashlib.sha1(
        (identity_id + ':v').encode(),
    ).hexdigest()
    identity_json = make_identity_version(name, email)

    log_raw = format_identity_log_raw(commit, version_blob)
    mock_git.run_routes['refs/identities/%s' % identity_id] = (
        0, log_raw,
    )
    mock_git.run_routes['cat-file blob %s' % version_blob] = (
        0, identity_json,
    )


def setup_single_bug(mock_git, reader, bid=BUG_ID, title='Test bug',
                     message='Bug description', timestamp=1700000000,
                     extra_ops=None, author_id=IDENTITY_ID):
    """Wire up mock routes for a single bug with one create op.

    Pre-caches the bug ID in the reader's resolve cache so tests
    don't need separate lines_routes for resolve_bug_id.
    """
    ops = [make_create_op(title, message, timestamp)]
    if extra_ops:
        ops.extend(extra_ops)
    pack_json = make_op_pack(author_id, ops)

    commit_hash = 'dead' * 10
    ops_blob = 'f00d' * 10
    log_raw = format_log_raw(commit_hash, ops_blob)

    mock_git.run_routes['refs/bugs/%s' % bid] = (0, log_raw)
    mock_git.batch_blobs[ops_blob] = pack_json

    # Pre-cache bug ID resolution
    reader._resolve_cache[bid] = bid

    # Identity resolution
    setup_identity(mock_git, author_id, 'Alice', 'alice@example.com')


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture()
def mock_git():
    """Patch ezgb git helpers with a routing mock.

    Returns a router whose ``.lines_routes``, ``.run_routes``, and
    ``.batch_blobs`` dicts control mocked return values.  Route keys
    are matched as substrings of the joined argument list.
    """
    class _Router:
        def __init__(self):
            self.lines_routes = {}
            self.run_routes = {}
            self.batch_blobs = {}

        def lines_side_effect(self, repo_path, args):
            joined = ' '.join(args)
            for key, value in self.lines_routes.items():
                if key in joined:
                    if callable(value):
                        return value(repo_path, args)
                    return value
            return []

        def run_side_effect(self, repo_path, args, stdin=None, decode=True):
            joined = ' '.join(args)
            for key, value in self.run_routes.items():
                if key in joined:
                    if callable(value):
                        return value(
                            repo_path, args, stdin=stdin, decode=decode,
                        )
                    return value
            return (0, '')

    router = _Router()

    # Default handler for batched blob reads
    def _batch_handler(repo_path, args, stdin=None, decode=True):
        pieces = []
        for line in stdin.decode().strip().splitlines():
            h = line.strip()
            if h in router.batch_blobs:
                content = router.batch_blobs[h].encode()
                hdr = ('%s blob %d\n' % (h, len(content))).encode()
                pieces.append(hdr + content + b'\n')
            else:
                pieces.append(('%s missing\n' % h).encode())
        return (0, b''.join(pieces))

    router.run_routes['cat-file --batch'] = _batch_handler

    # CLI side-effect for git_bug_cli (writer operations)
    def _cli_side_effect(repo_path, args):
        joined = ' '.join(args)
        for key, value in router.run_routes.items():
            if key in joined:
                if callable(value):
                    result = value(None, args)
                else:
                    result = value
                if len(result) == 2:
                    return result[0], result[1], ''
                return result
        return (0, '', '')

    with patch('ezgb._reader.git_run',
               side_effect=router.run_side_effect), \
         patch('ezgb._reader.git_lines',
               side_effect=router.lines_side_effect), \
         patch('ezgb._writer.git_bug_cli',
               side_effect=_cli_side_effect), \
         patch('ezgb._git.git_bug_cli',
               side_effect=_cli_side_effect):
        yield router


@pytest.fixture()
def reader():
    """Create a BugReader for the test repo."""
    return BugReader(REPO_PATH)


@pytest.fixture()
def writer(reader):
    """Create a BugWriter for the test repo."""
    return BugWriter(REPO_PATH, reader)
