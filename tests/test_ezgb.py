"""Tests for ezgb, ported from bugspray's test_gitbug.py.

Covers the reader (git object parsing, op-pack replay, caching),
writer (CLI-based mutations), and GitBugRepo facade.

Uses real git objects in temporary bare repos (via pygit2) instead
of subprocess mocking. See conftest.py for fixtures and helpers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pygit2
import pytest
from conftest import (
    BUG_ID,
    IDENTITY_ID,
    _create_bug_commit,
    _create_identity_commit,
    _MockWriter,
    make_comment_op,
    make_create_op,
    make_edit_comment_op,
    make_label_change_op,
    make_noop_op,
    make_op_pack,
    make_set_metadata_op,
    make_set_status_op,
    make_set_title_op,
    setup_identity,
    setup_single_bug,
)

from ezgb import (
    AmbiguousBugIdError,
    BugNotFoundError,
    BugSummary,
    GitBugRepo,
    Status,
    UnsupportedFormatError,
)
from ezgb._reader import BugReader, _combine_ids

# ------------------------------------------------------------------
# Unit tests: _combine_ids
# ------------------------------------------------------------------


class TestCombineIds:
    """Unit tests for the CombinedId interleaving function."""

    def test_interleave_pattern(self) -> None:
        primary = '0' * 64
        secondary = '1' * 64
        result = _combine_ids(primary, secondary)
        assert len(result) == 64
        # Secondary chars at positions 1, 3, 5, 9 and i>=10 where i%5==4
        for i, ch in enumerate(result):
            if i in (1, 3, 5, 9) or (i >= 10 and i % 5 == 4):
                assert ch == '1', 'position %d should be secondary' % i
            else:
                assert ch == '0', 'position %d should be primary' % i

    def test_distinct_inputs(self) -> None:
        primary = 'abcdef' * 10 + 'abcd'
        secondary = '123456' * 10 + '1234'
        result = _combine_ids(primary, secondary)
        assert len(result) == 64
        assert any(c in 'abcdef' for c in result)
        assert any(c in '123456' for c in result)


# ------------------------------------------------------------------
# Bug ID resolution
# ------------------------------------------------------------------


class TestResolveBugId:
    def test_resolves_full_id(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        ops_json = make_op_pack(IDENTITY_ID, [make_create_op('t', 'm')])
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, ops_json)
        result = reader.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_resolves_prefix(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        ops_json = make_op_pack(IDENTITY_ID, [make_create_op('t', 'm')])
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, ops_json)
        prefix = BUG_ID[:7]
        result = reader.resolve_bug_id(prefix)
        assert result == BUG_ID

    def test_caches_result(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        ops_json = make_op_pack(IDENTITY_ID, [make_create_op('t', 'm')])
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, ops_json)
        reader.resolve_bug_id(BUG_ID)
        # Second call should use cache
        result = reader.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_ambiguous_raises(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        prefix = 'abc'
        bid2 = 'abc' + 'd' * 61
        bid3 = 'abc' + 'e' * 61
        ops_json = make_op_pack(IDENTITY_ID, [make_create_op('t', 'm')])
        _create_bug_commit(repo, 'refs/bugs/%s' % bid2, ops_json)
        _create_bug_commit(repo, 'refs/bugs/%s' % bid3, ops_json)
        with pytest.raises(AmbiguousBugIdError, match='matches 2 bugs'):
            reader.resolve_bug_id(prefix)

    def test_not_found_raises(self, reader: BugReader, repo_path: str) -> None:
        with pytest.raises(BugNotFoundError, match='no bug matching'):
            reader.resolve_bug_id('nonexistent')


# ------------------------------------------------------------------
# Identity resolution
# ------------------------------------------------------------------


class TestResolveIdentity:
    def test_resolves_by_id(self, reader: BugReader, repo_path: str) -> None:
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')
        identity = reader.resolve_identity(IDENTITY_ID)
        assert identity.name == 'Alice'
        assert identity.email == 'alice@example.com'
        assert identity.login == 'alice'
        assert identity.id == IDENTITY_ID

    def test_caches_result(self, reader: BugReader, repo_path: str) -> None:
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')
        id1 = reader.resolve_identity(IDENTITY_ID)
        id2 = reader.resolve_identity(IDENTITY_ID)
        assert id1 is id2

    def test_fallback_on_missing(self, reader: BugReader, repo_path: str) -> None:
        identity = reader.resolve_identity(IDENTITY_ID)
        assert identity.name == IDENTITY_ID
        assert identity.email == IDENTITY_ID

    def test_unsupported_format_raises(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        identity_json = json.dumps(
            {
                'version': 99,
                'name': 'Alice',
                'email': 'alice@example.com',
            }
        )
        refname = 'refs/identities/%s' % IDENTITY_ID
        _create_identity_commit(repo, refname, identity_json)
        with pytest.raises(UnsupportedFormatError, match='identity format'):
            reader.resolve_identity(IDENTITY_ID)


# ------------------------------------------------------------------
# Bug snapshot reconstruction
# ------------------------------------------------------------------


class TestBuildBug:
    def test_snapshot_reconstruction(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        bug = reader.build_bug(BUG_ID)
        assert bug.id == BUG_ID
        assert bug.title == 'Test bug'
        assert bug.status == Status.OPEN
        assert bug.creator.name == 'Alice'
        assert bug.creator.email == 'alice@example.com'
        assert len(bug.comments) == 1
        assert bug.comments[0].text == 'Bug description'
        assert bug.comments[0].count == 0

    def test_set_title_updates(self, reader: BugReader, repo_path: str) -> None:
        title_op = make_set_title_op('Updated title')
        setup_single_bug(repo_path, reader, extra_ops=[title_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Updated title'

    def test_set_status_closed(self, reader: BugReader, repo_path: str) -> None:
        status_op = make_set_status_op(2)  # STATUS_CLOSED
        setup_single_bug(repo_path, reader, extra_ops=[status_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.status == Status.CLOSED

    def test_assigned_label(self, reader: BugReader, repo_path: str) -> None:
        assign_op = make_label_change_op(added=['assigned:bob@example.com'])
        setup_single_bug(repo_path, reader, extra_ops=[assign_op])
        bug = reader.build_bug(BUG_ID)
        assert 'assigned:bob@example.com' in bug.labels

    def test_label_add_and_remove(self, reader: BugReader, repo_path: str) -> None:
        add_op = make_label_change_op(added=['bug', 'priority/high'])
        rm_op = make_label_change_op(
            removed=['bug'],
            timestamp=1700005000,
        )
        setup_single_bug(repo_path, reader, extra_ops=[add_op, rm_op])
        bug = reader.build_bug(BUG_ID)
        assert 'priority/high' in bug.labels
        assert 'bug' not in bug.labels

    def test_add_comment_op(self, reader: BugReader, repo_path: str) -> None:
        comment_op = make_comment_op('A follow-up', timestamp=1700001000)
        setup_single_bug(repo_path, reader, extra_ops=[comment_op])
        bug = reader.build_bug(BUG_ID)
        assert len(bug.comments) == 2
        assert bug.comments[0].count == 0
        assert bug.comments[0].text == 'Bug description'
        assert bug.comments[1].count == 1
        assert bug.comments[1].text == 'A follow-up'

    def test_comment_with_attachment(self, reader: BugReader, repo_path: str) -> None:
        ops = [
            {
                'type': 1,
                'timestamp': 1700000000,
                'title': 'Bug with file',
                'message': 'See attached',
                'files': ['blobhash123'],
            }
        ]
        setup_single_bug(
            repo_path,
            reader,
            title='Bug with file',
            message='See attached',
            extra_ops=None,
        )
        # Rebuild with custom ops containing files
        reader.invalidate(BUG_ID)
        repo = pygit2.Repository(repo_path)
        # Delete old ref and create new one with files in the op
        repo.references.delete('refs/bugs/%s' % BUG_ID)
        pack_json = make_op_pack(IDENTITY_ID, ops)
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, pack_json)
        reader._resolve_cache[BUG_ID] = BUG_ID

        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].attachment_ids == ['blobhash123']

    def test_edit_comment(self, reader: BugReader, repo_path: str) -> None:
        """OP_EDIT_COMMENT whose target matches the create op hash
        should update the comment text."""
        # First, build the pack JSON for the create op so we can
        # compute the correct hash from the raw serialized op.
        create_op = make_create_op('Test bug', 'Original text')
        pack_json = make_op_pack(IDENTITY_ID, [create_op])
        # Extract the raw op JSON string from the pack
        raw_ops = BugReader._extract_raw_ops(pack_json)
        target_hash = BugReader._op_hash(raw_ops[0])

        edit_op = make_edit_comment_op(
            target=target_hash,
            message='Edited text',
        )
        # Build the full pack with both ops
        ops = [create_op, edit_op]
        full_pack_json = make_op_pack(IDENTITY_ID, ops)

        repo = pygit2.Repository(repo_path)
        _create_bug_commit(
            repo,
            'refs/bugs/%s' % BUG_ID,
            full_pack_json,
        )
        reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')

        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].text == 'Edited text'

    def test_edit_comment_unmatched_target(
        self, reader: BugReader, repo_path: str
    ) -> None:
        """Unmatched OP_EDIT_COMMENT target leaves text unchanged."""
        edit_op = make_edit_comment_op(
            target='nonexistent',
            message='Edited text',
        )
        setup_single_bug(repo_path, reader, extra_ops=[edit_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].text == 'Bug description'

    def test_metadata_from_set_metadata(
        self, reader: BugReader, repo_path: str
    ) -> None:
        meta_op = make_set_metadata_op({'key': 'value'})
        setup_single_bug(repo_path, reader, extra_ops=[meta_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.metadata == {'key': 'value'}

    def test_noop_ignored(self, reader: BugReader, repo_path: str) -> None:
        noop = make_noop_op()
        setup_single_bug(repo_path, reader, extra_ops=[noop])
        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Test bug'

    def test_multiple_op_packs(self, reader: BugReader, repo_path: str) -> None:
        """Operations spread across multiple commits are replayed
        in order."""
        pack2_json = make_op_pack(
            IDENTITY_ID,
            [
                make_set_title_op('Updated'),
            ],
        )
        setup_single_bug(
            repo_path,
            reader,
            title='Original',
            message='Body',
            extra_packs=[pack2_json],
        )
        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Updated'

    def test_missing_bug_raises(self, reader: BugReader, repo_path: str) -> None:
        reader._resolve_cache['nonexistent'] = 'nonexistent'
        with pytest.raises(BugNotFoundError, match='no operation packs'):
            reader.build_bug('nonexistent')

    def test_caching(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        bug1 = reader.build_bug(BUG_ID)
        bug2 = reader.build_bug(BUG_ID)
        assert bug1 is bug2

    def test_unsupported_format_raises(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        ops_json = make_op_pack(
            IDENTITY_ID,
            [
                make_create_op('Test', 'body'),
            ],
        )
        _create_bug_commit(
            repo,
            'refs/bugs/%s' % BUG_ID,
            ops_json,
            version_tag='version-99',
        )
        reader._resolve_cache[BUG_ID] = BUG_ID
        with pytest.raises(UnsupportedFormatError):
            reader.build_bug(BUG_ID)


# ------------------------------------------------------------------
# Bug summary (lightweight list-view snapshot)
# ------------------------------------------------------------------


class TestBuildBugSummary:
    def test_basic(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        s = reader.build_bug_summary(BUG_ID)
        assert isinstance(s, BugSummary)
        assert s.id == BUG_ID
        assert s.title == 'Test bug'
        assert s.status == Status.OPEN
        assert s.creator_id == IDENTITY_ID
        assert s.comment_count == 1  # create op has a message

    def test_author_name_resolved(self, reader: BugReader, repo_path: str) -> None:
        # The native path must resolve the creator's display name so
        # list views match summaries from the git-bug CLI cache.
        setup_single_bug(repo_path, reader)
        s = reader.build_bug_summary(BUG_ID)
        assert s.author_name == 'Alice'

    def test_edited_at_tracks_latest_op(
        self, reader: BugReader, repo_path: str
    ) -> None:
        comment_op = make_comment_op('A follow-up', timestamp=1700009000)
        setup_single_bug(repo_path, reader, extra_ops=[comment_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.edited_at == datetime.fromtimestamp(1700009000, tz=timezone.utc)
        assert s.edited_at > s.created_at

    def test_edited_at_defaults_to_created(
        self, reader: BugReader, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)
        s = reader.build_bug_summary(BUG_ID)
        assert s.edited_at == s.created_at

    def test_set_title(self, reader: BugReader, repo_path: str) -> None:
        title_op = make_set_title_op('Updated title')
        setup_single_bug(repo_path, reader, extra_ops=[title_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.title == 'Updated title'

    def test_set_status(self, reader: BugReader, repo_path: str) -> None:
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(repo_path, reader, extra_ops=[status_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.status == Status.CLOSED

    def test_label_changes(self, reader: BugReader, repo_path: str) -> None:
        add_op = make_label_change_op(added=['bug', 'priority/high'])
        rm_op = make_label_change_op(
            removed=['bug'],
            timestamp=1700005000,
        )
        setup_single_bug(repo_path, reader, extra_ops=[add_op, rm_op])
        s = reader.build_bug_summary(BUG_ID)
        assert 'priority/high' in s.labels
        assert 'bug' not in s.labels
        assert isinstance(s.labels, frozenset)

    def test_comment_count(self, reader: BugReader, repo_path: str) -> None:
        comment_op = make_comment_op('A follow-up')
        setup_single_bug(repo_path, reader, extra_ops=[comment_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 2  # create message + add_comment

    def test_create_without_message(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        ops = [make_create_op('No body', '')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, pack_json)
        reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')

        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 0

    def test_edit_comment_does_not_affect_count(
        self, reader: BugReader, repo_path: str
    ) -> None:
        edit_op = make_edit_comment_op(
            target='whatever',
            message='Edited',
        )
        setup_single_bug(repo_path, reader, extra_ops=[edit_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 1  # only the create message

    def test_metadata_skipped(self, reader: BugReader, repo_path: str) -> None:
        meta_op = make_set_metadata_op({'key': 'value'})
        setup_single_bug(repo_path, reader, extra_ops=[meta_op])
        s = reader.build_bug_summary(BUG_ID)
        assert not hasattr(s, 'metadata')

    def test_noop_ignored(self, reader: BugReader, repo_path: str) -> None:
        noop = make_noop_op()
        setup_single_bug(repo_path, reader, extra_ops=[noop])
        s = reader.build_bug_summary(BUG_ID)
        assert s.title == 'Test bug'

    def test_caching(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        s1 = reader.build_bug_summary(BUG_ID)
        s2 = reader.build_bug_summary(BUG_ID)
        assert s1 is s2

    def test_separate_cache(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader.build_bug_summary(BUG_ID)
        assert BUG_ID in reader._summary_cache
        assert BUG_ID not in reader._bug_cache

    def test_missing_raises(self, reader: BugReader, repo_path: str) -> None:
        reader._resolve_cache['nonexistent'] = 'nonexistent'
        with pytest.raises(BugNotFoundError, match='no operation packs'):
            reader.build_bug_summary('nonexistent')

    def test_invalidate_single(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader.build_bug_summary(BUG_ID)
        assert BUG_ID in reader._summary_cache
        reader.invalidate(BUG_ID)
        assert BUG_ID not in reader._summary_cache

    def test_invalidate_all(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader.build_bug_summary(BUG_ID)
        reader.invalidate()
        assert reader._summary_cache == {}


# ------------------------------------------------------------------
# Prefetch
# ------------------------------------------------------------------


class TestPrefetchBugs:
    def test_warms_cache(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader._bug_cache.clear()
        assert BUG_ID not in reader._bug_cache
        reader.prefetch_bugs([BUG_ID])
        assert BUG_ID in reader._bug_cache
        assert reader._bug_cache[BUG_ID].title == 'Test bug'

    def test_skips_cached(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        bug = reader.build_bug(BUG_ID)
        reader.prefetch_bugs([BUG_ID])
        assert reader._bug_cache[BUG_ID] is bug

    def test_empty_list(self, reader: BugReader, repo_path: str) -> None:
        reader.prefetch_bugs([])
        assert reader._bug_cache == {}


# ------------------------------------------------------------------
# Ref enumeration
# ------------------------------------------------------------------


class TestListBugRefs:
    def test_returns_refs(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        refs = reader.list_bug_refs()
        assert len(refs) == 1
        assert refs[0][0] == BUG_ID

    def test_empty(self, reader: BugReader, repo_path: str) -> None:
        refs = reader.list_bug_refs()
        assert refs == []


class TestListIdentityRefs:
    def test_returns_refs(self, reader: BugReader, repo_path: str) -> None:
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')
        refs = reader.list_identity_refs()
        assert len(refs) == 1
        assert refs[0][0] == IDENTITY_ID


# ------------------------------------------------------------------
# Cache management
# ------------------------------------------------------------------


class TestInvalidate:
    def test_invalidate_single_bug(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader.build_bug(BUG_ID)
        assert BUG_ID in reader._bug_cache
        reader.invalidate(BUG_ID)
        assert BUG_ID not in reader._bug_cache
        # Resolve cache preserved for single-bug invalidation
        assert BUG_ID in reader._resolve_cache

    def test_invalidate_all(self, reader: BugReader, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        reader.build_bug(BUG_ID)
        reader.invalidate()
        assert reader._bug_cache == {}
        assert reader._identity_cache == {}
        assert reader._resolve_cache == {}


# ------------------------------------------------------------------
# Writer: create_bug
# ------------------------------------------------------------------


class TestCreateBug:
    def test_creates_and_returns_bug(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        new_id = 'd' * 64
        prefix = new_id[:7]

        # CLI returns the created message
        writer._cli_routes['bug new'] = (0, '%s created\n' % prefix, '')

        # After create, invalidate() clears resolve cache, so we
        # need real git objects for resolution and building.
        repo = pygit2.Repository(repo_path)
        ops = [make_create_op('New bug', 'Details')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        _create_bug_commit(repo, 'refs/bugs/%s' % new_id, pack_json)
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')

        bug = writer.create_bug('New bug', 'Details')
        assert bug.id == new_id
        assert bug.title == 'New bug'
        assert bug.status == Status.OPEN


# ------------------------------------------------------------------
# Writer: add_comment
# ------------------------------------------------------------------


class TestAddComment:
    def test_adds_and_returns_comment(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)

        # CLI returns success
        writer._cli_routes['comment new'] = (0, 'comment added\n', '')

        # After cache invalidation, the rebuild reads updated ops.
        # Chain a second commit with the comment op onto the existing
        # bug ref (the first commit already has the create op).
        repo = pygit2.Repository(repo_path)
        ref = repo.references.get('refs/bugs/%s' % BUG_ID)
        assert ref is not None
        parent = ref.peel(pygit2.Commit)
        comment_op = make_comment_op('New comment', timestamp=1700005000)
        pack_json = make_op_pack(IDENTITY_ID, [comment_op])
        _create_bug_commit(
            repo,
            'refs/bugs/%s' % BUG_ID,
            pack_json,
            parent_oid=parent.id,
        )

        comment = writer.add_comment(BUG_ID, 'New comment')
        assert comment.text == 'New comment'
        assert comment.count == 1


# ------------------------------------------------------------------
# Writer: set_status
# ------------------------------------------------------------------


class TestSetStatus:
    def test_close(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)
        writer.set_status(BUG_ID, Status.CLOSED)
        assert any('close' in ' '.join(c) for c in writer._cli_calls)

    def test_open(self, reader: BugReader, writer: _MockWriter, repo_path: str) -> None:
        setup_single_bug(repo_path, reader)
        writer.set_status(BUG_ID, Status.OPEN)
        assert any('open' in ' '.join(c) for c in writer._cli_calls)


# ------------------------------------------------------------------
# Writer: set_title
# ------------------------------------------------------------------


class TestSetTitle:
    def test_updates_title(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)
        writer.set_title(BUG_ID, 'New title')
        assert any('New title' in ' '.join(c) for c in writer._cli_calls)


# ------------------------------------------------------------------
# Writer: labels
# ------------------------------------------------------------------


class TestLabels:
    def test_add_label(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)
        writer.add_label(BUG_ID, 'priority/high')
        assert any('priority/high' in ' '.join(c) for c in writer._cli_calls)

    def test_remove_label(
        self, reader: BugReader, writer: _MockWriter, repo_path: str
    ) -> None:
        setup_single_bug(repo_path, reader)
        writer.remove_label(BUG_ID, 'old-label')
        assert any('old-label' in ' '.join(c) for c in writer._cli_calls)


# ------------------------------------------------------------------
# ------------------------------------------------------------------
# GitBugRepo facade
# ------------------------------------------------------------------


class TestGitBugRepo:
    def test_get_bug(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        bug = repo_obj.get_bug(BUG_ID)
        assert bug.title == 'Test bug'

    def test_list_bugs(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        bugs = repo_obj.list_bugs()
        assert len(bugs) == 1
        assert bugs[0].title == 'Test bug'

    def test_list_bugs_filter_status(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(repo_path, repo_obj._reader, extra_ops=[status_op])

        bugs = repo_obj.list_bugs(status=Status.OPEN)
        assert len(bugs) == 0
        repo_obj.invalidate()
        bugs = repo_obj.list_bugs(status=Status.CLOSED)
        assert len(bugs) == 1

    def test_list_bugs_filter_label(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        label_op = make_label_change_op(added=['area/network'])
        setup_single_bug(repo_path, repo_obj._reader, extra_ops=[label_op])

        bugs = repo_obj.list_bugs(label='area/network')
        assert len(bugs) == 1
        repo_obj.invalidate()
        bugs = repo_obj.list_bugs(label='nonexistent')
        assert len(bugs) == 0

    def test_resolve_bug_id(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        repo = pygit2.Repository(repo_path)
        ops_json = make_op_pack(IDENTITY_ID, [make_create_op('t', 'm')])
        _create_bug_commit(repo, 'refs/bugs/%s' % BUG_ID, ops_json)
        result = repo_obj.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_list_identities(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')
        identities = repo_obj.list_identities()
        assert len(identities) == 1
        assert identities[0].name == 'Alice'

    def test_iter_bugs(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        bugs = list(repo_obj.iter_bugs())
        assert len(bugs) == 1
        assert bugs[0].title == 'Test bug'

    def test_iter_bugs_is_lazy(self, repo_path: str) -> None:
        """iter_bugs yields one at a time without prefetching all."""
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        it = repo_obj.iter_bugs()
        # Nothing built yet
        assert BUG_ID not in repo_obj._reader._bug_cache
        bug = next(it)
        assert bug.title == 'Test bug'

    def test_list_bug_summaries(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        summaries = repo_obj.list_bug_summaries()
        assert len(summaries) == 1
        assert isinstance(summaries[0], BugSummary)
        assert summaries[0].title == 'Test bug'
        assert summaries[0].creator_id == IDENTITY_ID

    def test_list_bug_summaries_filter_status(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(repo_path, repo_obj._reader, extra_ops=[status_op])

        assert len(repo_obj.list_bug_summaries(status=Status.OPEN)) == 0
        repo_obj.invalidate()
        assert len(repo_obj.list_bug_summaries(status=Status.CLOSED)) == 1

    def test_list_bug_summaries_filter_label(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        label_op = make_label_change_op(added=['area/network'])
        setup_single_bug(repo_path, repo_obj._reader, extra_ops=[label_op])

        assert len(repo_obj.list_bug_summaries(label='area/network')) == 1
        repo_obj.invalidate()
        assert len(repo_obj.list_bug_summaries(label='nonexistent')) == 0

    def test_iter_bug_summaries(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        setup_single_bug(repo_path, repo_obj._reader)
        summaries = list(repo_obj.iter_bug_summaries())
        assert len(summaries) == 1
        assert summaries[0].title == 'Test bug'

    def test_get_attachment(self, repo_path: str) -> None:
        repo_obj = GitBugRepo(repo_path)
        repo = pygit2.Repository(repo_path)
        blob_oid = repo.create_blob(b'file contents here')
        data = repo_obj.get_attachment(str(blob_oid))
        assert data == b'file contents here'

    def test_list_bugs_since(self, repo_path: str) -> None:
        """list_bugs(since=...) filters by committer timestamp."""
        repo_obj = GitBugRepo(repo_path)
        # Create bug with a specific commit timestamp
        repo = pygit2.Repository(repo_path)
        sig = pygit2.Signature('Test', 'test@test.com', time=1700005000, offset=0)
        ops = [make_create_op('Test bug', 'Bug description')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        ops_blob = repo.create_blob(pack_json.encode())
        version_blob = repo.create_blob(b'')
        tb = repo.TreeBuilder()
        tb.insert('ops', ops_blob, pygit2.GIT_FILEMODE_BLOB)
        tb.insert('version-4', version_blob, pygit2.GIT_FILEMODE_BLOB)
        tree_oid = tb.write()
        repo.create_commit(
            'refs/bugs/%s' % BUG_ID,
            sig,
            sig,
            'op pack',
            tree_oid,
            [],
        )
        repo_obj._reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')

        # since before the commit -- should match
        bugs = repo_obj.list_bugs(since=1700000000)
        assert len(bugs) == 1

        # since after the commit -- should not match
        repo_obj.invalidate()
        # Re-populate resolve cache after invalidation
        repo_obj._reader._resolve_cache[BUG_ID] = BUG_ID
        bugs = repo_obj.list_bugs(since=1700010000)
        assert len(bugs) == 0

    def test_list_bugs_since_string(self, repo_path: str) -> None:
        """list_bugs(since='2023-11-14 ...') parses the string."""
        repo_obj = GitBugRepo(repo_path)
        repo = pygit2.Repository(repo_path)
        sig = pygit2.Signature('Test', 'test@test.com', time=1700005000, offset=0)
        ops = [make_create_op('Test bug', 'Bug description')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        ops_blob = repo.create_blob(pack_json.encode())
        version_blob = repo.create_blob(b'')
        tb = repo.TreeBuilder()
        tb.insert('ops', ops_blob, pygit2.GIT_FILEMODE_BLOB)
        tb.insert('version-4', version_blob, pygit2.GIT_FILEMODE_BLOB)
        tree_oid = tb.write()
        repo.create_commit(
            'refs/bugs/%s' % BUG_ID,
            sig,
            sig,
            'op pack',
            tree_oid,
            [],
        )
        repo_obj._reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(repo_path, IDENTITY_ID, 'Alice', 'alice@example.com')

        bugs = repo_obj.list_bugs(since='2023-11-14 00:00:00')
        assert len(bugs) == 1


# ------------------------------------------------------------------
# Attachment reading
# ------------------------------------------------------------------


class TestCatBlobBytes:
    def test_reads_bytes(self, reader: BugReader, repo_path: str) -> None:
        repo = pygit2.Repository(repo_path)
        blob_oid = repo.create_blob(b'binary content')
        data = reader.cat_blob_bytes(str(blob_oid))
        assert data == b'binary content'
        assert isinstance(data, bytes)

    def test_missing_blob_raises(self, reader: BugReader, repo_path: str) -> None:
        with pytest.raises(BugNotFoundError):
            reader.cat_blob_bytes('ff' * 20)


# ------------------------------------------------------------------
# Since filtering on list_bug_refs
# ------------------------------------------------------------------


class TestListBugRefsSince:
    def _create_bug_with_timestamp(
        self, repo_path: str, reader: BugReader, ts: int
    ) -> None:
        """Helper: create a bug ref with a specific commit timestamp."""
        repo = pygit2.Repository(repo_path)
        sig = pygit2.Signature('Test', 'test@test.com', time=ts, offset=0)
        ops = [make_create_op('Test bug', 'description')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        ops_blob = repo.create_blob(pack_json.encode())
        version_blob = repo.create_blob(b'')
        tb = repo.TreeBuilder()
        tb.insert('ops', ops_blob, pygit2.GIT_FILEMODE_BLOB)
        tb.insert('version-4', version_blob, pygit2.GIT_FILEMODE_BLOB)
        tree_oid = tb.write()
        repo.create_commit(
            'refs/bugs/%s' % BUG_ID,
            sig,
            sig,
            'op pack',
            tree_oid,
            [],
        )

    def test_no_filter(self, reader: BugReader, repo_path: str) -> None:
        self._create_bug_with_timestamp(repo_path, reader, 1700005000)
        refs = reader.list_bug_refs()
        assert len(refs) == 1

    def test_since_includes_newer(self, reader: BugReader, repo_path: str) -> None:
        self._create_bug_with_timestamp(repo_path, reader, 1700005000)
        refs = reader.list_bug_refs(since=1700000000)
        assert len(refs) == 1

    def test_since_excludes_older(self, reader: BugReader, repo_path: str) -> None:
        self._create_bug_with_timestamp(repo_path, reader, 1700005000)
        refs = reader.list_bug_refs(since=1700010000)
        assert len(refs) == 0
