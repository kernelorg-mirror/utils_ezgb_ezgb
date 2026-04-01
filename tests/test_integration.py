"""Integration tests using real git repos and the git-bug binary.

These tests create ephemeral git repositories, initialize git-bug
with a test identity, and exercise the full ezgb stack end-to-end.
Skip automatically when ``git-bug`` is not installed.
"""
import json
import shutil
import subprocess

import pytest

from ezgb import GitBugRepo, Status

pytestmark = pytest.mark.skipif(
    shutil.which('git-bug') is None,
    reason='git-bug binary not found',
)


@pytest.fixture()
def gb_repo(tmp_path):
    """Spin up a temporary git repo with git-bug and a test identity."""
    repo_dir = tmp_path / 'repo'
    repo_dir.mkdir()
    repo_path = str(repo_dir)

    def _run(args, **kwargs):
        return subprocess.run(
            args, capture_output=True, text=True, check=True, **kwargs,
        )

    # Initialise the git repo
    _run(['git', 'init', repo_path])
    _run(['git', '-C', repo_path, 'config', 'user.name', 'Test User'])
    _run(['git', '-C', repo_path, 'config', 'user.email',
          'test@example.com'])
    _run(['git', '-C', repo_path, 'commit', '--allow-empty', '-m', 'init'])

    # Create and adopt a git-bug identity
    _run(['git', '-C', repo_path, 'bug', 'user', 'new',
          '-n', 'Test User', '-e', 'test@example.com',
          '--non-interactive'])
    result = _run(['git', '-C', repo_path, 'bug', 'user', '-f', 'json'])
    users = json.loads(result.stdout)
    _run(['git', '-C', repo_path, 'bug', 'user', 'adopt', users[0]['id']])

    return GitBugRepo(repo_path)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestCreateAndRead:
    def test_round_trip(self, gb_repo):
        bug = gb_repo.create_bug('Test bug', 'Description text')
        assert bug.title == 'Test bug'
        assert bug.status == Status.OPEN
        assert len(bug.comments) == 1
        assert bug.comments[0].text == 'Description text'
        assert bug.creator.name == 'Test User'
        assert bug.creator.email == 'test@example.com'
        assert len(bug.id) == 64

        # Read back through the reader path
        gb_repo.invalidate()
        bug2 = gb_repo.get_bug(bug.id)
        assert bug2.id == bug.id
        assert bug2.title == 'Test bug'

    def test_resolve_by_prefix(self, gb_repo):
        bug = gb_repo.create_bug('Prefix test', 'Body')
        prefix = bug.id[:8]
        full_id = gb_repo.resolve_bug_id(prefix)
        assert full_id == bug.id


class TestComments:
    def test_add_comment(self, gb_repo):
        bug = gb_repo.create_bug('Comment test', 'Original body')
        comment = gb_repo.add_comment(bug.id, 'Follow-up')
        assert comment.text == 'Follow-up'
        assert comment.count == 1

        bug = gb_repo.get_bug(bug.id)
        assert len(bug.comments) == 2
        assert bug.comments[0].text == 'Original body'
        assert bug.comments[1].text == 'Follow-up'

    def test_multiple_comments_ordering(self, gb_repo):
        bug = gb_repo.create_bug('Ordering test', 'c0')
        gb_repo.add_comment(bug.id, 'c1')
        gb_repo.add_comment(bug.id, 'c2')
        gb_repo.add_comment(bug.id, 'c3')

        bug = gb_repo.get_bug(bug.id)
        texts = [c.text for c in bug.comments]
        assert texts == ['c0', 'c1', 'c2', 'c3']
        counts = [c.count for c in bug.comments]
        assert counts == [0, 1, 2, 3]


class TestEditComment:
    def test_edit_comment_text(self, gb_repo):
        bug = gb_repo.create_bug('Edit comment test', 'Original body')
        comment = gb_repo.add_comment(bug.id, 'Original follow-up')

        gb_repo.edit_comment(bug.id, comment.id, 'Edited follow-up')
        bug = gb_repo.get_bug(bug.id)
        assert len(bug.comments) == 2
        assert bug.comments[1].text == 'Edited follow-up'

    def test_edit_preserves_other_comments(self, gb_repo):
        bug = gb_repo.create_bug('Preserve test', 'c0')
        c1 = gb_repo.add_comment(bug.id, 'c1')
        gb_repo.add_comment(bug.id, 'c2')

        gb_repo.edit_comment(bug.id, c1.id, 'c1-edited')
        bug = gb_repo.get_bug(bug.id)
        texts = [c.text for c in bug.comments]
        assert texts == ['c0', 'c1-edited', 'c2']

    def test_edit_to_tombstone(self, gb_repo):
        """Simulate the b4 bugs comment removal flow."""
        bug = gb_repo.create_bug('Tombstone test', 'Body')
        comment = gb_repo.add_comment(bug.id, 'Sensitive content')
        tombstone = (
            'Message-ID: <test@example.com>\n'
            'X-B4-Bug-Comment: removed by Test User <test@example.com>'
        )
        gb_repo.edit_comment(bug.id, comment.id, tombstone)

        bug = gb_repo.get_bug(bug.id)
        assert bug.comments[1].text == tombstone
        assert 'Sensitive content' not in bug.comments[1].text


class TestStatus:
    def test_close_and_reopen(self, gb_repo):
        bug = gb_repo.create_bug('Status test', 'Body')
        assert gb_repo.get_bug(bug.id).status == Status.OPEN

        gb_repo.set_status(bug.id, Status.CLOSED)
        bug = gb_repo.get_bug(bug.id)
        assert bug.status == Status.CLOSED

        gb_repo.set_status(bug.id, Status.OPEN)
        bug = gb_repo.get_bug(bug.id)
        assert bug.status == Status.OPEN


class TestTitle:
    def test_edit_title(self, gb_repo):
        bug = gb_repo.create_bug('Original title', 'Body')
        gb_repo.set_title(bug.id, 'Updated title')

        bug = gb_repo.get_bug(bug.id)
        assert bug.title == 'Updated title'


class TestLabels:
    def test_add_and_remove(self, gb_repo):
        bug = gb_repo.create_bug('Label test', 'Body')
        gb_repo.add_label(bug.id, 'priority/high')
        gb_repo.add_label(bug.id, 'area/network')

        bug = gb_repo.get_bug(bug.id)
        assert 'priority/high' in bug.labels
        assert 'area/network' in bug.labels

        gb_repo.remove_label(bug.id, 'priority/high')
        bug = gb_repo.get_bug(bug.id)
        assert 'priority/high' not in bug.labels
        assert 'area/network' in bug.labels


class TestListBugs:
    def test_list_all(self, gb_repo):
        gb_repo.create_bug('Bug one', 'First')
        gb_repo.create_bug('Bug two', 'Second')

        bugs = gb_repo.list_bugs()
        assert len(bugs) == 2
        titles = {b.title for b in bugs}
        assert titles == {'Bug one', 'Bug two'}

    def test_filter_by_status(self, gb_repo):
        gb_repo.create_bug('Open bug', 'Body')
        closed = gb_repo.create_bug('Closed bug', 'Body')
        gb_repo.set_status(closed.id, Status.CLOSED)

        gb_repo.invalidate()
        open_bugs = gb_repo.list_bugs(status=Status.OPEN)
        closed_bugs = gb_repo.list_bugs(status=Status.CLOSED)

        assert len(open_bugs) == 1
        assert open_bugs[0].title == 'Open bug'
        assert len(closed_bugs) == 1
        assert closed_bugs[0].title == 'Closed bug'

    def test_filter_by_label(self, gb_repo):
        labeled = gb_repo.create_bug('Labeled', 'Body')
        gb_repo.create_bug('Unlabeled', 'Body')
        gb_repo.add_label(labeled.id, 'important')

        gb_repo.invalidate()
        results = gb_repo.list_bugs(label='important')
        assert len(results) == 1
        assert results[0].title == 'Labeled'


class TestIdentities:
    def test_list_identities(self, gb_repo):
        identities = gb_repo.list_identities()
        assert len(identities) >= 1
        names = [i.name for i in identities]
        assert 'Test User' in names
        emails = [i.email for i in identities]
        assert 'test@example.com' in emails


class TestIterBugs:
    def test_yields_same_as_list(self, gb_repo):
        gb_repo.create_bug('Bug A', 'Body A')
        gb_repo.create_bug('Bug B', 'Body B')

        gb_repo.invalidate()
        listed = gb_repo.list_bugs()
        gb_repo.invalidate()
        iterated = list(gb_repo.iter_bugs())

        assert len(listed) == len(iterated) == 2
        assert {b.title for b in listed} == {b.title for b in iterated}

    def test_iter_with_filter(self, gb_repo):
        gb_repo.create_bug('Open one', 'Body')
        closed = gb_repo.create_bug('Closed one', 'Body')
        gb_repo.set_status(closed.id, Status.CLOSED)

        gb_repo.invalidate()
        open_bugs = list(gb_repo.iter_bugs(status=Status.OPEN))
        assert len(open_bugs) == 1
        assert open_bugs[0].title == 'Open one'


class TestSinceFilter:
    def test_list_bugs_since(self, gb_repo):
        """Bugs created before 'since' are excluded."""
        bug = gb_repo.create_bug('Recent bug', 'Body')
        # Use a timestamp far in the past -- should include the bug
        bugs = gb_repo.list_bugs(since=0)
        assert any(b.id == bug.id for b in bugs)

        # Use a timestamp far in the future -- should exclude
        gb_repo.invalidate()
        bugs = gb_repo.list_bugs(since=9999999999)
        assert len(bugs) == 0

    def test_since_with_iso_string(self, gb_repo):
        gb_repo.create_bug('String since', 'Body')
        gb_repo.invalidate()
        bugs = gb_repo.list_bugs(since='2020-01-01 00:00:00')
        assert len(bugs) >= 1


class TestAttachments:
    def test_get_attachment_round_trip(self, gb_repo):
        """Create a bug with an attachment, then read it back."""
        # Create a bug -- its description becomes comment 0
        bug = gb_repo.create_bug('Attachment test', 'See attached')
        bug = gb_repo.get_bug(bug.id)

        # Comments might not have attachments in this simple case,
        # but we can at least verify get_attachment works on a known
        # blob. Use the ops blob itself as a stand-in.
        # Instead, test against an actual blob by writing one via git.
        import subprocess
        repo_path = gb_repo._repo
        result = subprocess.run(
            ['git', '-C', repo_path, 'hash-object', '-w', '--stdin'],
            input=b'test file content',
            capture_output=True, check=True,
        )
        blob_hash = result.stdout.decode().strip()

        data = gb_repo.get_attachment(blob_hash)
        assert data == b'test file content'


class TestCache:
    def test_invalidate_and_reread(self, gb_repo):
        bug = gb_repo.create_bug('Cache test', 'Body')
        bug1 = gb_repo.get_bug(bug.id)
        gb_repo.invalidate(bug.id)
        bug2 = gb_repo.get_bug(bug.id)
        assert bug1 is not bug2
        assert bug1.title == bug2.title
        assert bug1.id == bug2.id

    def test_full_invalidate(self, gb_repo):
        bug = gb_repo.create_bug('Full invalidate', 'Body')
        gb_repo.get_bug(bug.id)
        gb_repo.invalidate()
        # Should rebuild cleanly from git objects
        bug2 = gb_repo.get_bug(bug.id)
        assert bug2.title == 'Full invalidate'
