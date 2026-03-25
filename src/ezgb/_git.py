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
    sp = subprocess.Popen(
        cmdargs,
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, _err = sp.communicate(input=stdin)
    if decode:
        return sp.returncode, out.decode(errors='replace')
    return sp.returncode, out


def git_lines(repo_path: str, args: list[str]) -> list[str]:
    """Run a git command and return non-empty output lines."""
    ecode, out = git_run(repo_path, args)
    if not isinstance(out, str):
        out = out.decode(errors='replace')
    return [line for line in out.split('\n') if line]


def git_bug_cli(
    repo_path: str, args: list[str],
) -> tuple[int, str, str]:
    """Run ``git -C REPO bug <args>`` for write operations.

    Uses ``-C`` (not ``--git-dir``) because the git-bug CLI
    needs a working tree context.

    Returns ``(returncode, stdout, stderr)``.
    """
    cmdargs = ['git', '--no-pager', '-C', repo_path, 'bug'] + args
    sp = subprocess.Popen(
        cmdargs,
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = sp.communicate()
    return sp.returncode, out.decode(errors='replace'), err.decode(errors='replace')
