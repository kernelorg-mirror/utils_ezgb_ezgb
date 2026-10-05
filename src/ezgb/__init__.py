#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""ezgb: a standalone Python library for git-bug repositories."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from ezgb._models import (
    AmbiguousBugIdError,
    Bug,
    BugNotFoundError,
    BugSummary,
    CliError,
    Comment,
    EzgbError,
    GitError,
    Identity,
    IdentityNotFoundError,
    Status,
    UnsupportedFormatError,
)
from ezgb._reader import BugReader, parse_since
from ezgb._types import JsonValue
from ezgb._writer import BugWriter

__all__ = [
    'GitBugRepo',
    'Bug',
    'BugSummary',
    'Comment',
    'Identity',
    'Status',
    'EzgbError',
    'BugNotFoundError',
    'AmbiguousBugIdError',
    'IdentityNotFoundError',
    'UnsupportedFormatError',
    'GitError',
    'CliError',
]


class GitBugRepo:
    """High-level interface to a git-bug repository.

    Reads bug data directly from git objects for speed. Writes go
    through the ``git bug`` CLI to maintain Lamport clock and DAG
    consistency.

    Usage::

        repo = GitBugRepo('/path/to/repo')
        for bug in repo.list_bugs(status=Status.OPEN):
            print(bug.title)
    """

    def __init__(self, repo_path: str) -> None:
        self._repo = repo_path
        self._reader = BugReader(repo_path)
        self._writer = BugWriter(repo_path, self._reader)

    # -- Read operations -----------------------------------------------------

    @staticmethod
    def _since_to_ts(since: str | datetime | int | None) -> int:
        """Normalise a *since* value to a unix timestamp (0 = no filter)."""
        if since is None:
            return 0
        if isinstance(since, int):
            return since
        if isinstance(since, datetime):
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            return int(since.timestamp())
        # str -- delegate to the reader's parser
        return parse_since(since)

    def list_bugs(
        self,
        *,
        status: Status | None = None,
        label: str | None = None,
        since: str | datetime | int | None = None,
    ) -> list[Bug]:
        """List all bugs, optionally filtered by status, label, and/or
        modification time.

        *since* accepts a unix timestamp (``int``), a
        :class:`~datetime.datetime`, or a string in ISO-8601 /
        ``YYYYMMDDHHMMSS`` format.  Only bugs whose tip commit is
        newer than this value are returned.
        """
        return list(
            self.iter_bugs(
                status=status,
                label=label,
                since=since,
            )
        )

    def iter_bugs(
        self,
        *,
        status: Status | None = None,
        label: str | None = None,
        since: str | datetime | int | None = None,
    ) -> Iterator[Bug]:
        """Lazily iterate over bugs, yielding one at a time.

        Accepts the same filters as :meth:`list_bugs` but avoids
        building all snapshots up front, which saves memory on large
        repositories.
        """
        ts = self._since_to_ts(since)
        refs = self._reader.list_bug_refs(since=ts)
        for bid, _commit in refs:
            try:
                bug = self._reader.build_bug(bid)
            except BugNotFoundError:
                continue
            if status is not None and bug.status != status:
                continue
            if label is not None and label not in bug.labels:
                continue
            yield bug

    def list_bug_summaries(
        self,
        *,
        status: Status | None = None,
        label: str | None = None,
        since: str | datetime | int | None = None,
    ) -> list[BugSummary]:
        """List lightweight bug summaries, optionally filtered.

        Uses the git-bug CLI cache when available (~100ms for
        hundreds of bugs). Falls back to native git object reads
        when the CLI is not installed or the cache is empty.
        """
        # Fast path: try the git-bug CLI cache
        if since is None:
            cached = self._list_summaries_from_cli()
            if cached is not None:
                results = cached
                if status is not None:
                    results = [s for s in results if s.status == status]
                if label is not None:
                    results = [s for s in results if label in s.labels]
                return results
        # Slow path: native git object reads
        return list(
            self.iter_bug_summaries(
                status=status,
                label=label,
                since=since,
            )
        )

    def _list_summaries_from_cli(self) -> list[BugSummary] | None:
        """Try to list summaries via the git-bug CLI cache.

        Returns None if the CLI is unavailable or returns an error,
        signalling the caller to fall back to native reads.
        """
        import json
        import shutil

        if shutil.which('git-bug') is None:
            return None

        from ezgb._git import git_bug_cli

        ecode, out, _err = git_bug_cli(self._repo, ['bug', '-f', 'json'])
        if ecode != 0 or not out.strip():
            return None
        try:
            raw_bugs: JsonValue = json.loads(out)
        except json.JSONDecodeError:
            return None
        assert isinstance(raw_bugs, list)
        results: list[BugSummary] = []
        for raw in raw_bugs:
            assert isinstance(raw, dict)
            bid = str(raw.get('id', ''))
            if not bid:
                continue
            status_str = str(raw.get('status', 'open'))
            bug_status = Status.CLOSED if status_str == 'closed' else Status.OPEN
            create_time = raw.get('create_time') or {}
            edit_time = raw.get('edit_time') or {}
            ct = create_time.get('timestamp', 0) if isinstance(create_time, dict) else 0
            if not isinstance(ct, int):
                ct = 0
            et = edit_time.get('timestamp', 0) if isinstance(edit_time, dict) else 0
            if not isinstance(et, int):
                et = 0
            author = raw.get('author') or {}
            author_name = author.get('name', '') if isinstance(author, dict) else ''
            if not isinstance(author_name, str):
                author_name = ''
            author_id = author.get('id', '') if isinstance(author, dict) else ''
            raw_labels = raw.get('labels') or []
            if isinstance(raw_labels, list):
                labels: frozenset[str] = frozenset(str(lb) for lb in raw_labels)
            else:
                labels = frozenset()
            comment_count = raw.get('comments', 0)
            if not isinstance(comment_count, int):
                comment_count = 0
            results.append(
                BugSummary(
                    id=bid,
                    title=str(raw.get('title', '')),
                    status=bug_status,
                    creator_id=str(author_id),
                    created_at=datetime.fromtimestamp(int(ct), tz=timezone.utc),
                    labels=labels,
                    comment_count=comment_count,
                    author_name=author_name,
                    edited_at=datetime.fromtimestamp(int(et), tz=timezone.utc),
                )
            )
        return results

    def iter_bug_summaries(
        self,
        *,
        status: Status | None = None,
        label: str | None = None,
        since: str | datetime | int | None = None,
    ) -> Iterator[BugSummary]:
        """Lazily iterate over bug summaries.

        Accepts the same filters as :meth:`list_bug_summaries`.
        """
        ts = self._since_to_ts(since)
        refs = self._reader.list_bug_refs(since=ts)
        for bid, _commit in refs:
            try:
                summary = self._reader.build_bug_summary(bid)
            except BugNotFoundError:
                continue
            if status is not None and summary.status != status:
                continue
            if label is not None and label not in summary.labels:
                continue
            yield summary

    def get_bug(self, bid: str) -> Bug:
        """Get a single bug by ID (full or abbreviated)."""
        bid = self._reader.resolve_bug_id(bid)
        return self._reader.build_bug(bid)

    def get_attachment(self, blob_hash: str) -> bytes:
        """Read a file attachment by its blob hash.

        Blob hashes are found in :attr:`Comment.attachment_ids`.
        """
        return self._reader.cat_blob_bytes(blob_hash)

    def search(self, query: str) -> list[Bug]:
        """Search bugs using the git-bug CLI query language.

        Delegates to ``git bug -f json <query>`` and reconstructs
        Bug objects from the results.
        """
        import json

        from ezgb._git import git_bug_cli

        ecode, out, _err = git_bug_cli(self._repo, ['bug', '-f', 'json', query])
        if ecode != 0:
            return []
        try:
            raw_bugs: JsonValue = json.loads(out)
        except json.JSONDecodeError:
            return []
        assert isinstance(raw_bugs, list)
        results: list[Bug] = []
        for raw in raw_bugs:
            assert isinstance(raw, dict)
            bid = str(raw.get('id', ''))
            if not bid:
                continue
            try:
                bug = self._reader.build_bug(bid)
                results.append(bug)
            except (BugNotFoundError, AmbiguousBugIdError):
                continue
        return results

    def list_identities(self) -> list[Identity]:
        """List all known identities."""
        refs = self._reader.list_identity_refs()
        identities: list[Identity] = []
        for iid, _commit in refs:
            identity = self._reader.resolve_identity(iid)
            identities.append(identity)
        return identities

    def resolve_bug_id(self, bid: str) -> str:
        """Resolve an abbreviated bug ID to the full 64-char ID."""
        return self._reader.resolve_bug_id(bid)

    # -- Write operations ----------------------------------------------------

    def create_bug(self, title: str, body: str) -> Bug:
        """Create a new bug and return its snapshot."""
        return self._writer.create_bug(title, body)

    def add_comment(self, bid: str, text: str) -> Comment:
        """Add a comment to a bug and return the new Comment."""
        return self._writer.add_comment(bid, text)

    def edit_comment(self, bid: str, comment_id: str, text: str) -> None:
        """Edit a comment's text."""
        self._writer.edit_comment(bid, comment_id, text)

    def set_status(self, bid: str, status: Status) -> None:
        """Set a bug's status to open or closed."""
        self._writer.set_status(bid, status)

    def set_title(self, bid: str, title: str) -> None:
        """Edit a bug's title."""
        self._writer.set_title(bid, title)

    def add_label(self, bid: str, label: str) -> None:
        """Add a label to a bug."""
        self._writer.add_label(bid, label)

    def remove_label(self, bid: str, label: str) -> None:
        """Remove a label from a bug."""
        self._writer.remove_label(bid, label)

    def remove_bug(self, bid: str) -> None:
        """Permanently delete a bug. This is irreversible."""
        self._writer.remove_bug(bid)

    def push(self, remote: str = 'origin') -> tuple[int, str, str]:
        """Push bugs and identities to a remote.

        Returns ``(returncode, stdout, stderr)``.
        """
        return self._writer.push(remote)

    def pull(self, remote: str = 'origin') -> tuple[int, str, str]:
        """Pull bugs and identities from a remote and merge.

        Returns ``(returncode, stdout, stderr)``. Caches are
        invalidated automatically after pull.
        """
        result = self._writer.pull(remote)
        self._reader.invalidate()
        return result

    # -- Cache management ----------------------------------------------------

    def invalidate(self, bid: str | None = None) -> None:
        """Clear cached data.

        If *bid* is given, only that bug is evicted. Otherwise all
        caches are cleared.
        """
        self._reader.invalidate(bid)
