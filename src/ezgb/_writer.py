#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""CLI wrappers for git-bug write operations."""
from __future__ import annotations

import logging
import re

from ezgb._git import git_bug_cli
from ezgb._models import (
    Bug,
    CliError,
    Comment,
    Status,
)
from ezgb._reader import BugReader

logger = logging.getLogger('ezgb')

# Matches "abc1234 created\n" from git bug new
_NEW_BUG_RE = re.compile(r'^([0-9a-f]{7,})\s+created')


class BugWriter:
    """Wraps the ``git bug`` CLI for write operations.

    All mutations go through the CLI to maintain Lamport clock and
    DAG consistency. The reader's cache is invalidated after each
    write so subsequent reads reflect the change.
    """

    def __init__(self, repo_path: str, reader: BugReader) -> None:
        self._repo = repo_path
        self._reader = reader

    def _cli(self, args: list[str]) -> tuple[int, str, str]:
        """Run a git-bug CLI command."""
        return git_bug_cli(self._repo, args)

    def create_bug(self, title: str, body: str) -> Bug:
        """Create a new bug and return its snapshot.

        Parses the human_id from ``git bug new`` output, then
        resolves and builds the full Bug.
        """
        args = ['bug', 'new', '-t', title, '-m', body, '--non-interactive']
        ecode, out, err = self._cli(args)
        if ecode != 0:
            raise CliError('git bug new failed: %s' % err)

        # Parse human_id from output like "abc1234 created"
        match = _NEW_BUG_RE.match(out.strip())
        if not match:
            raise CliError(
                'could not parse bug ID from git bug new output: %s' % out
            )
        human_id = match.group(1)

        self._reader.invalidate()
        full_bid = self._reader.resolve_bug_id(human_id)
        return self._reader.build_bug(full_bid)

    def add_comment(self, bid: str, text: str) -> Comment:
        """Add a comment to a bug and return the new Comment."""
        bid = self._reader.resolve_bug_id(bid)
        args = [
            'bug', 'comment', 'new', bid,
            '-m', text, '--non-interactive',
        ]
        ecode, out, err = self._cli(args)
        if ecode != 0:
            raise CliError('git bug comment new failed: %s' % err)
        self._reader.invalidate(bid)
        bug = self._reader.build_bug(bid)
        return bug.comments[-1]

    def set_status(self, bid: str, status: Status) -> None:
        """Set a bug's status to open or closed."""
        bid = self._reader.resolve_bug_id(bid)
        if status == Status.CLOSED:
            subcmd = 'close'
        else:
            subcmd = 'open'
        ecode, out, err = self._cli(['bug', 'status', subcmd, bid])
        if ecode != 0:
            raise CliError('git bug status %s failed: %s' % (subcmd, err))
        self._reader.invalidate(bid)

    def set_title(self, bid: str, title: str) -> None:
        """Edit a bug's title."""
        bid = self._reader.resolve_bug_id(bid)
        args = ['bug', 'title', 'edit', bid, '-t', title]
        ecode, out, err = self._cli(args)
        if ecode != 0:
            raise CliError('git bug title edit failed: %s' % err)
        self._reader.invalidate(bid)

    def add_label(self, bid: str, label: str) -> None:
        """Add a label to a bug."""
        bid = self._reader.resolve_bug_id(bid)
        ecode, out, err = self._cli(
            ['bug', 'label', 'new', bid, label],
        )
        if ecode != 0:
            raise CliError('git bug label new failed: %s' % err)
        self._reader.invalidate(bid)

    def remove_label(self, bid: str, label: str) -> None:
        """Remove a label from a bug."""
        bid = self._reader.resolve_bug_id(bid)
        ecode, out, err = self._cli(
            ['bug', 'label', 'rm', bid, label],
        )
        if ecode != 0:
            raise CliError('git bug label rm failed: %s' % err)
        self._reader.invalidate(bid)

    def assign(self, bid: str, email: str) -> None:
        """Assign a bug to an email address.

        Removes any existing ``assigned:*`` labels and adds a new
        ``assigned:{email}`` label.
        """
        bid = self._reader.resolve_bug_id(bid)
        # Remove existing assignment labels
        bug = self._reader.build_bug(bid)
        for lbl in bug.labels:
            if lbl.startswith('assigned:'):
                self.remove_label(bid, lbl)
        self.add_label(bid, 'assigned:%s' % email)

    def unassign(self, bid: str) -> None:
        """Remove any assignment from a bug."""
        bid = self._reader.resolve_bug_id(bid)
        bug = self._reader.build_bug(bid)
        for lbl in bug.labels:
            if lbl.startswith('assigned:'):
                self.remove_label(bid, lbl)
