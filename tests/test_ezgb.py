"""Tests for ezgb, ported from bugspray's test_gitbug.py.

Covers the reader (git object parsing, op-pack replay, caching),
writer (CLI-based mutations), and GitBugRepo facade.
"""
import json

import pytest
from conftest import (
    BUG_ID,
    IDENTITY_ID,
    REPO_PATH,
    format_identity_log_raw,
    format_log_raw,
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
# Unit tests: _parse_log_raw
# ------------------------------------------------------------------

class TestParseLogRaw:
    """Unit tests for BugReader._parse_log_raw()."""

    def test_single_commit(self):
        output = (
            'aa' * 20 + '\n'
            ':100644 100644 ' + '0' * 40 + ' ' + 'bb' * 20 + ' A\tops\n'
        )
        entries = BugReader._parse_log_raw(output)
        assert entries == [('aa' * 20, 'bb' * 20)]

    def test_multiple_commits(self):
        output = (
            'aa' * 20 + '\n' +
            ':100644 100644 ' + '0' * 40 + ' ' + 'bb' * 20 + ' A\tops\n' +
            '\n' +
            'cc' * 20 + '\n' +
            ':100644 100644 ' + '0' * 40 + ' ' + 'dd' * 20 + ' M\tops\n'
        )
        entries = BugReader._parse_log_raw(output)
        assert len(entries) == 2
        assert entries[0] == ('aa' * 20, 'bb' * 20)
        assert entries[1] == ('cc' * 20, 'dd' * 20)

    def test_skips_non_ops_files(self):
        output = (
            'aa' * 20 + '\n'
            ':100644 100644 ' + '0' * 40 + ' ' + 'bb' * 20 + ' A\tREADME\n'
        )
        entries = BugReader._parse_log_raw(output)
        assert entries == []

    def test_version_target(self):
        """Passing target_file='version' matches identity blobs."""
        output = (
            'aa' * 20 + '\n'
            ':100644 100644 ' + '0' * 40 + ' ' + 'bb' * 20 + ' A\tversion\n'
        )
        entries = BugReader._parse_log_raw(output, target_file='version')
        assert entries == [('aa' * 20, 'bb' * 20)]

    def test_blank_lines_skipped(self):
        output = '\n\n' + 'aa' * 20 + '\n\n'
        entries = BugReader._parse_log_raw(output)
        assert entries == []

    def test_empty_input(self):
        assert BugReader._parse_log_raw('') == []


# ------------------------------------------------------------------
# Unit tests: _batch_cat_blobs
# ------------------------------------------------------------------

class TestBatchCatBlobs:
    """Unit tests for BugReader._batch_cat_blobs()."""

    def test_empty_input(self, reader, mock_git):
        result = reader._batch_cat_blobs([])
        assert result == {}

    def test_single_blob(self, reader, mock_git):
        mock_git.batch_blobs['aa' * 20] = '{"hello": "world"}'
        result = reader._batch_cat_blobs(['aa' * 20])
        assert result == {'aa' * 20: '{"hello": "world"}'}

    def test_missing_object(self, reader, mock_git):
        result = reader._batch_cat_blobs(['ff' * 20])
        assert result == {}

    def test_mixed_present_and_missing(self, reader, mock_git):
        mock_git.batch_blobs['aa' * 20] = 'content-a'
        result = reader._batch_cat_blobs(['aa' * 20, 'ff' * 20])
        assert 'aa' * 20 in result
        assert 'ff' * 20 not in result


# ------------------------------------------------------------------
# Unit tests: _combine_ids
# ------------------------------------------------------------------

class TestCombineIds:
    """Unit tests for the CombinedId interleaving function."""

    def test_interleave_pattern(self):
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

    def test_distinct_inputs(self):
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
    def test_resolves_full_id(self, reader, mock_git):
        mock_git.lines_routes[BUG_ID] = ['refs/bugs/%s' % BUG_ID]
        result = reader.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_resolves_prefix(self, reader, mock_git):
        prefix = BUG_ID[:7]
        mock_git.lines_routes[prefix] = ['refs/bugs/%s' % BUG_ID]
        result = reader.resolve_bug_id(prefix)
        assert result == BUG_ID

    def test_caches_result(self, reader, mock_git):
        mock_git.lines_routes[BUG_ID] = ['refs/bugs/%s' % BUG_ID]
        reader.resolve_bug_id(BUG_ID)
        # Second call should use cache — clear routes to prove it
        mock_git.lines_routes.clear()
        result = reader.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_ambiguous_raises(self, reader, mock_git):
        prefix = 'abc'
        bid2 = 'abc' + 'd' * 61
        bid3 = 'abc' + 'e' * 61
        mock_git.lines_routes[prefix] = [
            'refs/bugs/%s' % bid2,
            'refs/bugs/%s' % bid3,
        ]
        with pytest.raises(AmbiguousBugIdError, match='matches 2 bugs'):
            reader.resolve_bug_id(prefix)

    def test_not_found_raises(self, reader, mock_git):
        mock_git.lines_routes['nonexistent'] = []
        with pytest.raises(BugNotFoundError, match='no bug matching'):
            reader.resolve_bug_id('nonexistent')


# ------------------------------------------------------------------
# Identity resolution
# ------------------------------------------------------------------

class TestResolveIdentity:
    def test_resolves_by_id(self, reader, mock_git):
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')
        identity = reader.resolve_identity(IDENTITY_ID)
        assert identity.name == 'Alice'
        assert identity.email == 'alice@example.com'
        assert identity.login == 'alice'
        assert identity.id == IDENTITY_ID

    def test_caches_result(self, reader, mock_git):
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')
        id1 = reader.resolve_identity(IDENTITY_ID)
        id2 = reader.resolve_identity(IDENTITY_ID)
        assert id1 is id2

    def test_fallback_on_missing(self, reader, mock_git):
        mock_git.run_routes['refs/identities/%s' % IDENTITY_ID] = (0, '')
        identity = reader.resolve_identity(IDENTITY_ID)
        assert identity.name == IDENTITY_ID
        assert identity.email == IDENTITY_ID

    def test_unsupported_format_raises(self, reader, mock_git):
        identity_json = json.dumps({
            'version': 99,
            'name': 'Alice',
            'email': 'alice@example.com',
        })
        commit = 'aa' * 20
        version_blob = 'bb' * 20
        log_raw = format_identity_log_raw(commit, version_blob)
        mock_git.run_routes['refs/identities/%s' % IDENTITY_ID] = (
            0, log_raw,
        )
        mock_git.run_routes['cat-file blob %s' % version_blob] = (
            0, identity_json,
        )
        with pytest.raises(UnsupportedFormatError, match='identity format'):
            reader.resolve_identity(IDENTITY_ID)


# ------------------------------------------------------------------
# Bug snapshot reconstruction
# ------------------------------------------------------------------

class TestBuildBug:
    def test_snapshot_reconstruction(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        bug = reader.build_bug(BUG_ID)
        assert bug.id == BUG_ID
        assert bug.title == 'Test bug'
        assert bug.status == Status.OPEN
        assert bug.creator.name == 'Alice'
        assert bug.creator.email == 'alice@example.com'
        assert len(bug.comments) == 1
        assert bug.comments[0].text == 'Bug description'
        assert bug.comments[0].count == 0

    def test_set_title_updates(self, reader, mock_git):
        title_op = make_set_title_op('Updated title')
        setup_single_bug(mock_git, reader, extra_ops=[title_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Updated title'

    def test_set_status_closed(self, reader, mock_git):
        status_op = make_set_status_op(2)  # STATUS_CLOSED
        setup_single_bug(mock_git, reader, extra_ops=[status_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.status == Status.CLOSED

    def test_assigned_to_from_label(self, reader, mock_git):
        assign_op = make_label_change_op(added=['assigned:bob@example.com'])
        setup_single_bug(mock_git, reader, extra_ops=[assign_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.assigned_to == 'bob@example.com'

    def test_label_add_and_remove(self, reader, mock_git):
        add_op = make_label_change_op(added=['bug', 'priority/high'])
        rm_op = make_label_change_op(
            removed=['bug'], timestamp=1700005000,
        )
        setup_single_bug(mock_git, reader, extra_ops=[add_op, rm_op])
        bug = reader.build_bug(BUG_ID)
        assert 'priority/high' in bug.labels
        assert 'bug' not in bug.labels

    def test_add_comment_op(self, reader, mock_git):
        comment_op = make_comment_op('A follow-up', timestamp=1700001000)
        setup_single_bug(mock_git, reader, extra_ops=[comment_op])
        bug = reader.build_bug(BUG_ID)
        assert len(bug.comments) == 2
        assert bug.comments[0].count == 0
        assert bug.comments[0].text == 'Bug description'
        assert bug.comments[1].count == 1
        assert bug.comments[1].text == 'A follow-up'

    def test_comment_with_attachment(self, reader, mock_git):
        ops = [{
            'type': 1,
            'timestamp': 1700000000,
            'title': 'Bug with file',
            'message': 'See attached',
            'files': ['blobhash123'],
        }]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        commit_hash = 'dead' * 10
        ops_blob = 'f00d' * 10
        log_raw = format_log_raw(commit_hash, ops_blob)
        mock_git.run_routes['refs/bugs/%s' % BUG_ID] = (0, log_raw)
        mock_git.batch_blobs[ops_blob] = pack_json
        reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')

        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].attachment_ids == ['blobhash123']

    def test_edit_comment(self, reader, mock_git):
        """OP_EDIT_COMMENT whose target matches the create op hash
        should update the comment text."""
        create_op = make_create_op('Test bug', 'Original text')
        target_hash = BugReader._op_hash(create_op)
        edit_op = make_edit_comment_op(
            target=target_hash, message='Edited text',
        )
        setup_single_bug(
            mock_git, reader,
            message='Original text', extra_ops=[edit_op],
        )
        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].text == 'Edited text'

    def test_edit_comment_unmatched_target(self, reader, mock_git):
        """Unmatched OP_EDIT_COMMENT target leaves text unchanged."""
        edit_op = make_edit_comment_op(
            target='nonexistent', message='Edited text',
        )
        setup_single_bug(mock_git, reader, extra_ops=[edit_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.comments[0].text == 'Bug description'

    def test_metadata_from_set_metadata(self, reader, mock_git):
        meta_op = make_set_metadata_op({'key': 'value'})
        setup_single_bug(mock_git, reader, extra_ops=[meta_op])
        bug = reader.build_bug(BUG_ID)
        assert bug.metadata == {'key': 'value'}

    def test_noop_ignored(self, reader, mock_git):
        noop = make_noop_op()
        setup_single_bug(mock_git, reader, extra_ops=[noop])
        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Test bug'

    def test_multiple_op_packs(self, reader, mock_git):
        """Operations spread across multiple commits are replayed
        in order."""
        pack1 = make_op_pack(IDENTITY_ID, [
            make_create_op('Original', 'Body'),
        ])
        pack2 = make_op_pack(IDENTITY_ID, [
            make_set_title_op('Updated'),
        ])

        commit1 = 'aa' * 20
        blob1 = 'bb' * 20
        commit2 = 'cc' * 20
        blob2 = 'dd' * 20

        log_raw = (
            format_log_raw(commit1, blob1)
            + format_log_raw(commit2, blob2)
        )
        mock_git.run_routes['refs/bugs/%s' % BUG_ID] = (0, log_raw)
        mock_git.batch_blobs[blob1] = pack1
        mock_git.batch_blobs[blob2] = pack2
        reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')

        bug = reader.build_bug(BUG_ID)
        assert bug.title == 'Updated'

    def test_missing_bug_raises(self, reader, mock_git):
        reader._resolve_cache['nonexistent'] = 'nonexistent'
        mock_git.run_routes['refs/bugs/nonexistent'] = (0, '')
        with pytest.raises(BugNotFoundError, match='no operation packs'):
            reader.build_bug('nonexistent')

    def test_caching(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        bug1 = reader.build_bug(BUG_ID)
        bug2 = reader.build_bug(BUG_ID)
        assert bug1 is bug2

    def test_unsupported_format_raises(self, reader, mock_git):
        commit_hash = 'dead' * 10
        ops_blob = 'f00d' * 10
        log_raw = (
            '%s\n'
            ':100644 100644 %s %s A\tops\n'
            ':100644 100644 %s %s A\tversion-99\n'
            % (commit_hash, '0' * 40, ops_blob, '0' * 40, '0' * 40)
        )
        mock_git.run_routes['refs/bugs/%s' % BUG_ID] = (0, log_raw)
        reader._resolve_cache[BUG_ID] = BUG_ID
        with pytest.raises(UnsupportedFormatError):
            reader.build_bug(BUG_ID)


# ------------------------------------------------------------------
# Bug summary (lightweight list-view snapshot)
# ------------------------------------------------------------------

class TestBuildBugSummary:
    def test_basic(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        s = reader.build_bug_summary(BUG_ID)
        assert isinstance(s, BugSummary)
        assert s.id == BUG_ID
        assert s.title == 'Test bug'
        assert s.status == Status.OPEN
        assert s.creator_id == IDENTITY_ID
        assert s.comment_count == 1  # create op has a message

    def test_set_title(self, reader, mock_git):
        title_op = make_set_title_op('Updated title')
        setup_single_bug(mock_git, reader, extra_ops=[title_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.title == 'Updated title'

    def test_set_status(self, reader, mock_git):
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(mock_git, reader, extra_ops=[status_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.status == Status.CLOSED

    def test_label_changes(self, reader, mock_git):
        add_op = make_label_change_op(added=['bug', 'priority/high'])
        rm_op = make_label_change_op(
            removed=['bug'], timestamp=1700005000,
        )
        setup_single_bug(mock_git, reader, extra_ops=[add_op, rm_op])
        s = reader.build_bug_summary(BUG_ID)
        assert 'priority/high' in s.labels
        assert 'bug' not in s.labels
        assert isinstance(s.labels, frozenset)

    def test_comment_count(self, reader, mock_git):
        comment_op = make_comment_op('A follow-up')
        setup_single_bug(mock_git, reader, extra_ops=[comment_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 2  # create message + add_comment

    def test_create_without_message(self, reader, mock_git):
        ops = [make_create_op('No body', '')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        commit_hash = 'dead' * 10
        ops_blob = 'f00d' * 10
        log_raw = format_log_raw(commit_hash, ops_blob)
        mock_git.run_routes['refs/bugs/%s' % BUG_ID] = (0, log_raw)
        mock_git.batch_blobs[ops_blob] = pack_json
        reader._resolve_cache[BUG_ID] = BUG_ID
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')

        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 0

    def test_edit_comment_does_not_affect_count(self, reader, mock_git):
        edit_op = make_edit_comment_op(
            target='whatever', message='Edited',
        )
        setup_single_bug(mock_git, reader, extra_ops=[edit_op])
        s = reader.build_bug_summary(BUG_ID)
        assert s.comment_count == 1  # only the create message

    def test_metadata_skipped(self, reader, mock_git):
        meta_op = make_set_metadata_op({'key': 'value'})
        setup_single_bug(mock_git, reader, extra_ops=[meta_op])
        s = reader.build_bug_summary(BUG_ID)
        assert not hasattr(s, 'metadata')

    def test_noop_ignored(self, reader, mock_git):
        noop = make_noop_op()
        setup_single_bug(mock_git, reader, extra_ops=[noop])
        s = reader.build_bug_summary(BUG_ID)
        assert s.title == 'Test bug'

    def test_caching(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        s1 = reader.build_bug_summary(BUG_ID)
        s2 = reader.build_bug_summary(BUG_ID)
        assert s1 is s2

    def test_separate_cache(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader.build_bug_summary(BUG_ID)
        assert BUG_ID in reader._summary_cache
        assert BUG_ID not in reader._bug_cache

    def test_missing_raises(self, reader, mock_git):
        reader._resolve_cache['nonexistent'] = 'nonexistent'
        mock_git.run_routes['refs/bugs/nonexistent'] = (0, '')
        with pytest.raises(BugNotFoundError, match='no operation packs'):
            reader.build_bug_summary('nonexistent')

    def test_invalidate_single(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader.build_bug_summary(BUG_ID)
        assert BUG_ID in reader._summary_cache
        reader.invalidate(BUG_ID)
        assert BUG_ID not in reader._summary_cache

    def test_invalidate_all(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader.build_bug_summary(BUG_ID)
        reader.invalidate()
        assert reader._summary_cache == {}


# ------------------------------------------------------------------
# Prefetch
# ------------------------------------------------------------------

class TestPrefetchBugs:
    def test_warms_cache(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader._bug_cache.clear()
        assert BUG_ID not in reader._bug_cache
        reader.prefetch_bugs([BUG_ID])
        assert BUG_ID in reader._bug_cache
        assert reader._bug_cache[BUG_ID].title == 'Test bug'

    def test_skips_cached(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        bug = reader.build_bug(BUG_ID)
        reader.prefetch_bugs([BUG_ID])
        assert reader._bug_cache[BUG_ID] is bug

    def test_empty_list(self, reader, mock_git):
        reader.prefetch_bugs([])
        assert reader._bug_cache == {}


# ------------------------------------------------------------------
# Ref enumeration
# ------------------------------------------------------------------

class TestListBugRefs:
    def test_returns_refs(self, reader, mock_git):
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        refs = reader.list_bug_refs()
        assert len(refs) == 1
        assert refs[0] == (BUG_ID, commit)

    def test_empty(self, reader, mock_git):
        mock_git.lines_routes['refs/bugs/'] = []
        refs = reader.list_bug_refs()
        assert refs == []


class TestListIdentityRefs:
    def test_returns_refs(self, reader, mock_git):
        commit = 'beef' * 10
        mock_git.lines_routes['refs/identities/'] = [
            'identities/%s %s' % (IDENTITY_ID, commit),
        ]
        refs = reader.list_identity_refs()
        assert len(refs) == 1
        assert refs[0] == (IDENTITY_ID, commit)


# ------------------------------------------------------------------
# Cache management
# ------------------------------------------------------------------

class TestInvalidate:
    def test_invalidate_single_bug(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader.build_bug(BUG_ID)
        assert BUG_ID in reader._bug_cache
        reader.invalidate(BUG_ID)
        assert BUG_ID not in reader._bug_cache
        # Resolve cache preserved for single-bug invalidation
        assert BUG_ID in reader._resolve_cache

    def test_invalidate_all(self, reader, mock_git):
        setup_single_bug(mock_git, reader)
        reader.build_bug(BUG_ID)
        reader.invalidate()
        assert reader._bug_cache == {}
        assert reader._identity_cache == {}
        assert reader._resolve_cache == {}


# ------------------------------------------------------------------
# Writer: create_bug
# ------------------------------------------------------------------

class TestCreateBug:
    def test_creates_and_returns_bug(self, reader, writer, mock_git):
        new_id = 'd' * 64
        prefix = new_id[:7]

        # CLI returns the created message
        mock_git.run_routes['bug new'] = (0, '%s created\n' % prefix)

        # After create, invalidate() clears resolve cache, so
        # set up for-each-ref to resolve the prefix
        mock_git.lines_routes[prefix] = ['refs/bugs/%s' % new_id]

        # Bug data for the newly created bug
        commit_hash = 'a1b2' * 10
        ops_blob = 'e5f6' * 10
        ops = [make_create_op('New bug', 'Details')]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        log_raw = format_log_raw(commit_hash, ops_blob)
        mock_git.run_routes['refs/bugs/%s' % new_id] = (0, log_raw)
        mock_git.batch_blobs[ops_blob] = pack_json
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')

        bug = writer.create_bug('New bug', 'Details')
        assert bug.id == new_id
        assert bug.title == 'New bug'
        assert bug.status == Status.OPEN


# ------------------------------------------------------------------
# Writer: add_comment
# ------------------------------------------------------------------

class TestAddComment:
    def test_adds_and_returns_comment(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)

        # CLI returns success
        mock_git.run_routes['comment new'] = (0, 'comment added\n')

        # After cache invalidation, the rebuild reads updated ops.
        comment_op = make_comment_op('New comment', timestamp=1700005000)
        ops = [make_create_op('Test bug', 'Bug description'), comment_op]
        pack_json = make_op_pack(IDENTITY_ID, ops)
        mock_git.batch_blobs['f00d' * 10] = pack_json

        comment = writer.add_comment(BUG_ID, 'New comment')
        assert comment.text == 'New comment'
        assert comment.count == 1


# ------------------------------------------------------------------
# Writer: set_status
# ------------------------------------------------------------------

class TestSetStatus:
    def test_close(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['status close'] = _handler

        writer.set_status(BUG_ID, Status.CLOSED)
        assert any('close' in ' '.join(c) for c in calls)

    def test_open(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['status open'] = _handler

        writer.set_status(BUG_ID, Status.OPEN)
        assert any('open' in ' '.join(c) for c in calls)


# ------------------------------------------------------------------
# Writer: set_title
# ------------------------------------------------------------------

class TestSetTitle:
    def test_updates_title(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['title edit'] = _handler

        writer.set_title(BUG_ID, 'New title')
        assert any('New title' in ' '.join(c) for c in calls)


# ------------------------------------------------------------------
# Writer: labels
# ------------------------------------------------------------------

class TestLabels:
    def test_add_label(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['label new'] = _handler

        writer.add_label(BUG_ID, 'priority/high')
        assert any('priority/high' in ' '.join(c) for c in calls)

    def test_remove_label(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['label rm'] = _handler

        writer.remove_label(BUG_ID, 'old-label')
        assert any('old-label' in ' '.join(c) for c in calls)


# ------------------------------------------------------------------
# Writer: assign / unassign
# ------------------------------------------------------------------

class TestAssign:
    def test_assign(self, reader, writer, mock_git):
        setup_single_bug(mock_git, reader)
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['label new'] = _handler

        writer.assign(BUG_ID, 'bob@example.com')
        joined = ' '.join(' '.join(c) for c in calls)
        assert 'assigned:bob@example.com' in joined

    def test_unassign_removes_existing(self, reader, writer, mock_git):
        assign_op = make_label_change_op(
            added=['assigned:alice@example.com'],
        )
        setup_single_bug(mock_git, reader, extra_ops=[assign_op])
        calls = []

        def _handler(repo_path, args):
            calls.append(args)
            return (0, '')

        mock_git.run_routes['label rm'] = _handler

        writer.unassign(BUG_ID)
        joined = ' '.join(' '.join(c) for c in calls)
        assert 'assigned:alice@example.com' in joined


# ------------------------------------------------------------------
# GitBugRepo facade
# ------------------------------------------------------------------

class TestGitBugRepo:
    def test_get_bug(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        setup_single_bug(mock_git, repo._reader)
        bug = repo.get_bug(BUG_ID)
        assert bug.title == 'Test bug'

    def test_list_bugs(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)
        bugs = repo.list_bugs()
        assert len(bugs) == 1
        assert bugs[0].title == 'Test bug'

    def test_list_bugs_filter_status(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(mock_git, repo._reader, extra_ops=[status_op])

        bugs = repo.list_bugs(status=Status.OPEN)
        assert len(bugs) == 0
        # Invalidate so the cached CLOSED result is re-read
        repo.invalidate()
        bugs = repo.list_bugs(status=Status.CLOSED)
        assert len(bugs) == 1

    def test_list_bugs_filter_label(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        label_op = make_label_change_op(added=['area/network'])
        setup_single_bug(mock_git, repo._reader, extra_ops=[label_op])

        bugs = repo.list_bugs(label='area/network')
        assert len(bugs) == 1
        repo.invalidate()
        bugs = repo.list_bugs(label='nonexistent')
        assert len(bugs) == 0

    def test_resolve_bug_id(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        mock_git.lines_routes[BUG_ID] = ['refs/bugs/%s' % BUG_ID]
        result = repo.resolve_bug_id(BUG_ID)
        assert result == BUG_ID

    def test_list_identities(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'beef' * 10
        mock_git.lines_routes['refs/identities/'] = [
            'identities/%s %s' % (IDENTITY_ID, commit),
        ]
        setup_identity(mock_git, IDENTITY_ID, 'Alice', 'alice@example.com')

        identities = repo.list_identities()
        assert len(identities) == 1
        assert identities[0].name == 'Alice'

    def test_iter_bugs(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)
        bugs = list(repo.iter_bugs())
        assert len(bugs) == 1
        assert bugs[0].title == 'Test bug'

    def test_iter_bugs_is_lazy(self, mock_git):
        """iter_bugs yields one at a time without prefetching all."""
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)
        it = repo.iter_bugs()
        # Nothing built yet
        assert BUG_ID not in repo._reader._bug_cache
        bug = next(it)
        assert bug.title == 'Test bug'

    def test_list_bug_summaries(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)
        summaries = repo.list_bug_summaries()
        assert len(summaries) == 1
        assert isinstance(summaries[0], BugSummary)
        assert summaries[0].title == 'Test bug'
        assert summaries[0].creator_id == IDENTITY_ID

    def test_list_bug_summaries_filter_status(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        status_op = make_set_status_op(2)  # CLOSED
        setup_single_bug(mock_git, repo._reader, extra_ops=[status_op])

        assert len(repo.list_bug_summaries(status=Status.OPEN)) == 0
        repo.invalidate()
        assert len(repo.list_bug_summaries(status=Status.CLOSED)) == 1

    def test_list_bug_summaries_filter_label(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        label_op = make_label_change_op(added=['area/network'])
        setup_single_bug(mock_git, repo._reader, extra_ops=[label_op])

        assert len(repo.list_bug_summaries(label='area/network')) == 1
        repo.invalidate()
        assert len(repo.list_bug_summaries(label='nonexistent')) == 0

    def test_iter_bug_summaries(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)
        summaries = list(repo.iter_bug_summaries())
        assert len(summaries) == 1
        assert summaries[0].title == 'Test bug'

    def test_get_attachment(self, mock_git):
        repo = GitBugRepo(REPO_PATH)
        blob_hash = 'aa' * 20

        def _blob_handler(repo_path, args, stdin=None, decode=True):
            return (0, b'file contents here')

        mock_git.run_routes['cat-file blob %s' % blob_hash] = _blob_handler
        data = repo.get_attachment(blob_hash)
        assert data == b'file contents here'

    def test_list_bugs_since(self, mock_git):
        """list_bugs(since=...) filters by committer timestamp."""
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        # Ref with timestamp 1700005000
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s 1700005000' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)

        # since before the commit — should match
        bugs = repo.list_bugs(since=1700000000)
        assert len(bugs) == 1

        # since after the commit — should not match
        repo.invalidate()
        bugs = repo.list_bugs(since=1700010000)
        assert len(bugs) == 0

    def test_list_bugs_since_string(self, mock_git):
        """list_bugs(since='2023-11-14 ...') parses the string."""
        repo = GitBugRepo(REPO_PATH)
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s 1700005000' % (BUG_ID, commit),
        ]
        setup_single_bug(mock_git, repo._reader)

        bugs = repo.list_bugs(since='2023-11-14 00:00:00')
        assert len(bugs) == 1


# ------------------------------------------------------------------
# Attachment reading
# ------------------------------------------------------------------

class TestCatBlobBytes:
    def test_reads_bytes(self, reader, mock_git):
        blob_hash = 'aa' * 20

        def _handler(repo_path, args, stdin=None, decode=True):
            return (0, b'binary content')

        mock_git.run_routes['cat-file blob %s' % blob_hash] = _handler
        data = reader.cat_blob_bytes(blob_hash)
        assert data == b'binary content'
        assert isinstance(data, bytes)

    def test_missing_blob_raises(self, reader, mock_git):
        blob_hash = 'ff' * 20

        def _handler(repo_path, args, stdin=None, decode=True):
            return (128, b'')

        mock_git.run_routes['cat-file blob %s' % blob_hash] = _handler
        with pytest.raises(BugNotFoundError):
            reader.cat_blob_bytes(blob_hash)


# ------------------------------------------------------------------
# Since filtering on list_bug_refs
# ------------------------------------------------------------------

class TestListBugRefsSince:
    def test_no_filter(self, reader, mock_git):
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s' % (BUG_ID, commit),
        ]
        refs = reader.list_bug_refs()
        assert len(refs) == 1

    def test_since_includes_newer(self, reader, mock_git):
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s 1700005000' % (BUG_ID, commit),
        ]
        refs = reader.list_bug_refs(since=1700000000)
        assert len(refs) == 1

    def test_since_excludes_older(self, reader, mock_git):
        commit = 'dead' * 10
        mock_git.lines_routes['refs/bugs/'] = [
            'bugs/%s %s 1700005000' % (BUG_ID, commit),
        ]
        refs = reader.list_bug_refs(since=1700010000)
        assert len(refs) == 0
