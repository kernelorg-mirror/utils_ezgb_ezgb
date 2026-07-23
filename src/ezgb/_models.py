#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""Data models, enums, exceptions, and constants for ezgb."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone

_EPOCH_UTC = datetime.min.replace(tzinfo=timezone.utc)

# -- Exceptions --------------------------------------------------------------


class EzgbError(Exception):
    """Base exception for all ezgb errors."""


class BugNotFoundError(EzgbError):
    """A bug ID could not be resolved or has no operation packs."""


class AmbiguousBugIdError(EzgbError):
    """A bug ID prefix matches multiple bugs."""


class IdentityNotFoundError(EzgbError):
    """An identity ID could not be resolved."""


class UnsupportedFormatError(EzgbError):
    """Git-bug objects use an unsupported format version."""


class GitError(EzgbError):
    """A git subprocess failed unexpectedly."""


class CliError(EzgbError):
    """A git-bug CLI write operation failed."""


# -- Enums -------------------------------------------------------------------


class Status(enum.Enum):
    """Bug status matching git-bug's common.Status values."""

    OPEN = 1
    CLOSED = 2


# -- Constants ---------------------------------------------------------------

# git-bug format versions we support
SUPPORTED_BUG_FORMAT: int = 4
SUPPORTED_IDENTITY_FORMAT: int = 2

# Operation type constants (from entities/bug/operation.go)
OP_CREATE: int = 1
OP_SET_TITLE: int = 2
OP_ADD_COMMENT: int = 3
OP_SET_STATUS: int = 4
OP_LABEL_CHANGE: int = 5
OP_EDIT_COMMENT: int = 6
OP_NOOP: int = 7
OP_SET_METADATA: int = 8

# Status values used in operation JSON
STATUS_OPEN: int = 1
STATUS_CLOSED: int = 2


# -- Dataclasses -------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """A git-bug user identity."""

    id: str
    name: str
    email: str
    login: str = ''


@dataclass
class Comment:
    """A comment on a bug."""

    id: str
    author: Identity
    text: str
    created_at: datetime
    count: int
    attachment_ids: list[str] = field(default_factory=list)


@dataclass
class Bug:
    """A git-bug bug snapshot reconstructed from operations."""

    id: str
    title: str
    status: Status
    creator: Identity
    created_at: datetime
    labels: set[str]
    comments: list[Comment]
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BugSummary:
    """Lightweight bug snapshot for list views.

    Contains only the fields needed for display in listings.
    Identity resolution is deferred -- *creator_id* holds the raw
    identity hash string rather than a resolved Identity object.

    Both the git-bug CLI cache and native git-object reads populate
    *author_name* (the creator's resolved display name) and
    *edited_at* (the last-activity time). *author_name* may fall back
    to the raw identity hash if the identity cannot be resolved.
    """

    id: str
    title: str
    status: Status
    creator_id: str
    created_at: datetime
    labels: frozenset[str]
    comment_count: int
    author_name: str = ''
    edited_at: datetime = _EPOCH_UTC
