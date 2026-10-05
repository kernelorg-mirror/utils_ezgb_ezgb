#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""Thin git subprocess helpers for ezgb."""

from __future__ import annotations

import os
import subprocess
from typing import Union


def git_run(
    repo_path: str,
    args: list[str],
    stdin: bytes | None = None,
    decode: bool = True,
) -> tuple[int, Union[str, bytes]]:
    """Run a git command against a repository.

    Uses ``--git-dir`` so it works on both bare and non-bare repos.
    Automatically appends ``--no-abbrev-commit`` for ``log`` commands.
    """
    cmdargs = ['git', '--no-pager']
    gitdir = repo_path
    if os.path.isdir(os.path.join(repo_path, '.git')):
        gitdir = os.path.join(repo_path, '.git')
    cmdargs += ['--git-dir', gitdir]
    args = list(args)
    if args and args[0] == 'log':
        args.insert(1, '--no-abbrev-commit')
    cmdargs += args
    sp = subprocess.run(cmdargs, input=stdin, capture_output=True)
    if decode:
        return sp.returncode, sp.stdout.decode(errors='replace')
    return sp.returncode, sp.stdout


def git_lines(repo_path: str, args: list[str]) -> list[str]:
    """Run a git command and return non-empty output lines."""
    _ecode, out = git_run(repo_path, args)
    if not isinstance(out, str):
        out = out.decode(errors='replace')
    return [line for line in out.split('\n') if line]


def git_bug_cli(
    repo_path: str,
    args: list[str],
    stdin: str | None = None,
) -> tuple[int, str, str]:
    """Run ``git -C REPO bug <args>`` for write operations.

    Uses ``-C`` (not ``--git-dir``) because the git-bug CLI
    needs a working tree context.

    When *stdin* is provided it is piped to the process on stdin.

    Returns ``(returncode, stdout, stderr)``.
    """
    cmdargs = ['git', '--no-pager', '-C', repo_path, 'bug'] + args
    in_bytes = stdin.encode() if stdin is not None else None
    sp = subprocess.run(cmdargs, input=in_bytes, capture_output=True)
    return (
        sp.returncode,
        sp.stdout.decode(errors='replace'),
        sp.stderr.decode(errors='replace'),
    )
