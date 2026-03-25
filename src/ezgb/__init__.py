#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""ezgb: a standalone Python library for git-bug repositories."""
from __future__ import annotations

from ezgb._models import (
    AmbiguousBugIdError,
    Bug,
    BugNotFoundError,
    CliError,
    Comment,
    EzgbError,
    GitError,
    Identity,
    IdentityNotFoundError,
    Status,
    UnsupportedFormatError,
)
from ezgb._reader import BugReader
from ezgb._writer import BugWriter

__all__ = [
    'GitBugRepo',
    'Bug',
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

    def list_bugs(
        self,
        *,
        status: Status | None = None,
        label: str | None = None,
    ) -> list[Bug]:
        """List all bugs, optionally filtered by status and/or label."""
        refs = self._reader.list_bug_refs()
        bids = [bid for bid, _commit in refs]
        self._reader.prefetch_bugs(bids)
        results: list[Bug] = []
        for bid in bids:
            try:
                bug = self._reader.build_bug(bid)
            except BugNotFoundError:
                continue
            if status is not None and bug.status != status:
                continue
            if label is not None and label not in bug.labels:
                continue
            results.append(bug)
        return results

    def get_bug(self, bid: str) -> Bug:
        """Get a single bug by ID (full or abbreviated)."""
        bid = self._reader.resolve_bug_id(bid)
        return self._reader.build_bug(bid)

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
            raw_bugs: list[dict[str, object]] = json.loads(out)
        except json.JSONDecodeError:
            return []
        results: list[Bug] = []
        for raw in raw_bugs:
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

    def assign(self, bid: str, email: str) -> None:
        """Assign a bug to an email address."""
        self._writer.assign(bid, email)

    def unassign(self, bid: str) -> None:
        """Remove any assignment from a bug."""
        self._writer.unassign(bid)

    # -- Cache management ----------------------------------------------------

    def invalidate(self, bid: str | None = None) -> None:
        """Clear cached data.

        If *bid* is given, only that bug is evicted. Otherwise all
        caches are cleared.
        """
        self._reader.invalidate(bid)
