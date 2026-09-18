#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""Git object reading, operation pack replay, and caching for ezgb."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import TypeGuard, TypeVar

import pygit2

from ezgb._models import (
    OP_ADD_COMMENT,
    OP_CREATE,
    OP_EDIT_COMMENT,
    OP_LABEL_CHANGE,
    OP_NOOP,
    OP_SET_METADATA,
    OP_SET_STATUS,
    OP_SET_TITLE,
    STATUS_OPEN,
    SUPPORTED_BUG_FORMAT,
    SUPPORTED_IDENTITY_FORMAT,
    AmbiguousBugIdError,
    Bug,
    BugNotFoundError,
    BugSummary,
    Comment,
    Identity,
    Status,
    UnsupportedFormatError,
)
from ezgb._types import JsonObject, JsonValue

logger = logging.getLogger('ezgb')

# Length of a full git-bug entity ID (SHA-256 hex)
_ID_LENGTH = 64

_K = TypeVar('_K')
_T = TypeVar('_T')
_U = TypeVar('_U')


def _is_list(value: list[_T], item_ty: type[_U]) -> TypeGuard[list[_U]]:
    return all(isinstance(item, item_ty) for item in value)


def _is_dict_values(value: dict[_K, _T], value_ty: type[_U]) -> TypeGuard[dict[_K, _U]]:
    return all(isinstance(value, value_ty) for value in value.values())


def _combine_ids(primary: str, secondary: str) -> str:
    """Interleave primary and secondary IDs into a CombinedId.

    Mirrors git-bug's CombineIds function. The interleaving pattern
    places secondary characters at positions 1, 3, 5, 9, and then
    every position where ``i >= 10 and i % 5 == 4``.

    Format: PSPSPSPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPPSPPPP
    (P=primary, S=secondary)
    """
    result: list[str] = []
    pi = 0
    si = 0
    for i in range(_ID_LENGTH):
        if i in (1, 3, 5, 9) or (i >= 10 and i % 5 == 4):
            result.append(secondary[si])
            si += 1
        else:
            result.append(primary[pi])
            pi += 1
    return ''.join(result)


class BugReader:
    """Reads bug and identity data from git-bug's git object storage."""

    def __init__(self, repo_path: str) -> None:
        self._repo = repo_path
        gitdir = repo_path
        if os.path.isdir(os.path.join(repo_path, '.git')):
            gitdir = os.path.join(repo_path, '.git')
        self._pygit: pygit2.Repository = pygit2.Repository(gitdir)
        self._bug_cache: dict[str, Bug] = {}
        self._summary_cache: dict[str, BugSummary] = {}
        self._identity_cache: dict[str, Identity] = {}
        self._resolve_cache: dict[str, str] = {}

    # -- Blob reading (pygit2) -------------------------------------------------

    def _cat_blob(self, blob_hash: str) -> str:
        """Read a single git blob as text."""
        try:
            obj = self._pygit.get(blob_hash)
        except (ValueError, KeyError):
            obj = None
        if obj is None or not isinstance(obj, pygit2.Blob):
            raise BugNotFoundError('failed to read blob %s' % blob_hash)
        result: str = obj.data.decode(errors='replace')
        return result

    def cat_blob_bytes(self, blob_hash: str) -> bytes:
        """Read a single git blob as raw bytes."""
        try:
            obj = self._pygit.get(blob_hash)
        except (ValueError, KeyError):
            obj = None
        if obj is None or not isinstance(obj, pygit2.Blob):
            raise BugNotFoundError('failed to read blob %s' % blob_hash)
        return bytes(obj.data)

    def _walk_ref_tree_blobs(
        self,
        refname: str,
        target_file: str = 'ops',
    ) -> list[tuple[str, str]]:
        """Walk the commit chain for a ref and find named blobs.

        Returns ``[(commit_hex, blob_hex)]`` oldest first, for each
        commit whose tree contains *target_file*.
        """
        try:
            ref = self._pygit.references.get(refname)
        except (ValueError, KeyError):
            ref = None
        if ref is None:
            return []
        tip = ref.peel(pygit2.Commit)
        entries: list[tuple[str, str]] = []
        commit: pygit2.Commit | None = tip
        while commit is not None:
            tree = commit.tree
            if target_file in tree:
                blob_entry = tree[target_file]
                entries.append((str(commit.id), str(blob_entry.id)))
            commit = commit.parents[0] if commit.parents else None
        entries.reverse()
        return entries

    # -- Format version checking ---------------------------------------------

    @staticmethod
    def _check_format_version_tree(
        tree: pygit2.Tree,
        prefix: str,
        supported: int,
    ) -> None:
        """Validate the git-bug format version from a commit tree.

        Scans tree entries for names matching ``{prefix}{N}`` and
        raises UnsupportedFormatError if the version does not match.
        """
        for entry in tree:
            name: str = entry.name or ''
            if name.startswith(prefix):
                try:
                    version = int(name[len(prefix) :])
                except ValueError:
                    continue
                if version != supported:
                    raise UnsupportedFormatError(
                        'unsupported git-bug format: %s%d (expected %s%d)'
                        % (prefix, version, prefix, supported)
                    )
                return

    # -- Ref enumeration -----------------------------------------------------

    def _list_refs(
        self,
        prefix: str,
        *,
        since: int = 0,
    ) -> list[tuple[str, str]]:
        """Return ``[(entity_id, commit_hex)]`` for refs under *prefix*.

        If *since* is positive, only refs whose tip commit is newer
        than that unix timestamp are returned.
        """
        results: list[tuple[str, str]] = []
        for refname in self._pygit.references:
            if not refname.startswith(prefix):
                continue
            ref = self._pygit.references.get(refname)
            if ref is None:
                continue
            commit = ref.peel(pygit2.Commit)
            if since > 0 and commit.commit_time < since:
                continue
            eid = refname.rsplit('/', 1)[-1]
            results.append((eid, str(commit.id)))
        return results

    def list_bug_refs(
        self,
        *,
        since: int = 0,
    ) -> list[tuple[str, str]]:
        """Return ``[(bug_id, commit_hash)]`` for all bugs."""
        return self._list_refs('refs/bugs/', since=since)

    def list_identity_refs(self) -> list[tuple[str, str]]:
        """Return ``[(identity_id, commit_hash)]`` for all identities."""
        return self._list_refs('refs/identities/')

    # -- Bug ID resolution ---------------------------------------------------

    def resolve_bug_id(self, bid: str) -> str:
        """Resolve a possibly-abbreviated bug ID to the full ref name.

        Raises AmbiguousBugIdError when more than one ref matches.
        Raises BugNotFoundError when no ref matches.
        """
        if bid in self._resolve_cache:
            return self._resolve_cache[bid]
        prefix = 'refs/bugs/' + bid
        candidates: list[str] = [
            str(refname).rsplit('/', 1)[-1]
            for refname in self._pygit.references
            if str(refname).startswith(prefix)
        ]
        if len(candidates) == 1:
            full_bid = candidates[0]
            self._resolve_cache[bid] = full_bid
            self._resolve_cache[full_bid] = full_bid
            return full_bid
        if len(candidates) > 1:
            raise AmbiguousBugIdError(
                'bug ID prefix %s matches %d bugs' % (bid, len(candidates))
            )
        raise BugNotFoundError('no bug matching ID prefix %s' % bid)

    # -- Operation pack parsing ----------------------------------------------

    def _get_op_packs(
        self,
        bid: str,
    ) -> tuple[list[JsonObject], list[str]]:
        """Walk the commit chain for a bug and return operation packs
        (oldest first) together with the raw blob strings.

        Returns ``(packs, raw_blobs)`` where each element in
        *raw_blobs* is the original JSON text of the corresponding
        pack.  The raw strings are needed for correct operation
        hashing.
        """
        bid = self.resolve_bug_id(bid)
        refname = 'refs/bugs/' + bid
        entries = self._walk_ref_tree_blobs(refname, 'ops')
        if not entries:
            return [], []
        # Check format version from the first commit's tree
        ref = self._pygit.references.get(refname)
        if ref is not None:
            tip = ref.peel(pygit2.Commit)
            self._check_format_version_tree(tip.tree, 'version-', SUPPORTED_BUG_FORMAT)
        packs: list[JsonObject] = []
        raw_blobs: list[str] = []
        for _commit, blob_hash in entries:
            raw = self._cat_blob(blob_hash)
            try:
                pack: JsonValue = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    'failed to parse ops blob %s for bug %s',
                    blob_hash,
                    bid,
                )
                continue
            assert isinstance(pack, dict)
            packs.append(pack)
            raw_blobs.append(raw)
        return packs, raw_blobs

    # -- Identity resolution -------------------------------------------------

    def resolve_identity(self, identity_id: str) -> Identity:
        """Resolve an identity ID to an Identity dataclass.

        Reads the most recent version blob from the identity's commit
        chain. Identity versions use a flat JSON format (not op-packs).
        """
        if identity_id in self._identity_cache:
            return self._identity_cache[identity_id]

        fallback = Identity(
            id=identity_id,
            name=identity_id,
            email=identity_id,
        )
        refname = 'refs/identities/' + identity_id
        try:
            ref = self._pygit.references.get(refname)
        except (ValueError, KeyError):
            ref = None
        if ref is None:
            self._identity_cache[identity_id] = fallback
            return fallback

        # Get the tip commit's tree and look for 'version' blob
        tip = ref.peel(pygit2.Commit)
        if 'version' not in tip.tree:
            self._identity_cache[identity_id] = fallback
            return fallback

        version_blob = tip.tree['version']
        try:
            blob = self._pygit[version_blob.id]
            assert isinstance(blob, pygit2.Blob)
            raw = blob.data.decode(errors='replace')
            data: JsonValue = json.loads(raw)
        except (json.JSONDecodeError, KeyError):
            self._identity_cache[identity_id] = fallback
            return fallback
        assert isinstance(data, dict)

        # Validate format version
        fmt_version = data.get('version')
        if fmt_version is not None:
            assert isinstance(fmt_version, int)
        if fmt_version is not None and fmt_version != SUPPORTED_IDENTITY_FORMAT:
            raise UnsupportedFormatError(
                'unsupported identity format: %d (expected %d)'
                % (fmt_version, SUPPORTED_IDENTITY_FORMAT)
            )

        name = data.get('name', identity_id)
        email = data.get('email', identity_id)
        login = data.get('login', '')
        assert isinstance(name, str)
        assert isinstance(email, str)
        assert isinstance(login, str)
        identity = Identity(
            id=identity_id,
            name=name,
            email=email,
            login=login,
        )
        self._identity_cache[identity_id] = identity
        return identity

    # -- Bug snapshot building -----------------------------------------------

    @staticmethod
    def _op_hash(raw_op_json: str) -> str:
        """Compute a git-bug compatible operation ID.

        git-bug derives IDs as SHA256 of the compact JSON serialization.
        The raw JSON string from the blob must be used (not a
        re-serialized version) because Go's ``json.Marshal`` escapes
        characters like ``<``, ``>``, ``&`` as ``\\u003c`` etc., and
        Python's ``json.dumps`` does not reproduce that encoding.
        """
        return hashlib.sha256(raw_op_json.encode()).hexdigest()

    @staticmethod
    def _extract_raw_ops(blob_json: str) -> list[str]:
        """Extract individual operation JSON strings from an ops blob.

        git-bug identifies each operation by hashing its JSON
        serialization with SHA-256.  The serialization is produced by
        Go's ``json.Marshal``, which escapes ``<``, ``>``, and ``&``
        as ``\\u003c``, ``\\u003e``, ``\\u0026`` — a Go-specific
        behaviour that Python's ``json.dumps`` does not reproduce.
        Re-serializing in Python therefore produces different bytes
        and a different hash, which silently breaks
        ``OP_EDIT_COMMENT`` matching (the ``target`` field carries the
        Go-computed hash).

        To get correct hashes we must use the exact bytes that
        git-bug wrote.  This method locates each op object inside the
        original blob string using :meth:`json.JSONDecoder.raw_decode`
        and slices the string at those boundaries, preserving Go's
        escaping verbatim.
        """
        decoder = json.JSONDecoder()
        try:
            pack: JsonValue
            pack, _ = decoder.raw_decode(blob_json)
        except json.JSONDecodeError:
            return []
        assert isinstance(pack, dict)
        raw_ops = pack.get('ops')
        if not raw_ops:
            return []
        assert isinstance(raw_ops, list)
        # Walk forward through the string to slice each op.
        # Find the start of the ops array value.
        idx = blob_json.find('"ops"')
        pos = blob_json.find('[', idx) + 1
        ops: list[str] = []
        for _ in raw_ops:
            # raw_decode skips leading whitespace on its own, but
            # we need to step past commas between elements.
            while blob_json[pos] in ' \t\n\r,':
                pos += 1
            _decoded_op: JsonValue
            _decoded_op, end_pos = decoder.raw_decode(blob_json, pos)
            ops.append(blob_json[pos:end_pos])
            pos = end_pos
        return ops

    @staticmethod
    def _format_timestamp(unix_ts: int) -> datetime:
        """Convert a unix timestamp to a timezone-aware datetime."""
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc)

    @staticmethod
    def _parse_since(since: str) -> int:
        """Parse a datetime string to a unix timestamp.

        Accepts ISO-8601 variants and ``YYYYMMDDHHMMSS``.
        """
        since = since.strip()
        for fmt in (
            '%Y-%m-%d %H:%M:%S',
            '%Y%m%d%H%M%S',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S',
        ):
            try:
                dt = datetime.strptime(since, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                continue
        raise ValueError('cannot parse since=%s' % since)

    def build_bug(
        self,
        bid: str,
        packs: list[JsonObject] | None = None,
        raw_blobs: list[str] | None = None,
    ) -> Bug:
        """Reconstruct a bug snapshot by replaying operation packs.

        Returns a Bug dataclass. Results are cached by full bug ID.
        """
        bid = self.resolve_bug_id(bid)
        if bid in self._bug_cache:
            return self._bug_cache[bid]

        if packs is None:
            packs, raw_blobs = self._get_op_packs(bid)
        if not packs:
            raise BugNotFoundError('no operation packs for bug %s' % bid)
        if raw_blobs is None:
            raw_blobs = []

        # Extract raw op JSON strings from each blob for hashing
        raw_ops_per_pack: list[list[str]] = [
            self._extract_raw_ops(blob) for blob in raw_blobs
        ]

        title = ''
        is_open = True
        creator: Identity | None = None
        created_at: datetime | None = None
        labels: set[str] = set()
        comments: list[Comment] = []
        comment_count = 0
        metadata: dict[str, str] = {}
        # Map operation hashes to Comment objects for OP_EDIT_COMMENT
        op_hash_map: dict[str, Comment] = {}

        for pack_idx, pack in enumerate(packs):
            author_value = pack.get('author', {})
            assert isinstance(author_value, dict)
            author_id = author_value.get('id', '')
            assert isinstance(author_id, str)
            raw_ops = (
                raw_ops_per_pack[pack_idx] if pack_idx < len(raw_ops_per_pack) else []
            )
            ops = pack.get('ops', [])
            assert isinstance(ops, list)
            for op_idx, op in enumerate(ops):
                assert isinstance(op, dict)
                op_type = op.get('type', 0)
                assert isinstance(op_type, int)
                timestamp = op.get('timestamp', 0)
                assert isinstance(timestamp, int)
                raw_json = raw_ops[op_idx] if op_idx < len(raw_ops) else ''
                op_id = self._op_hash(raw_json) if raw_json else ''

                if op_type == OP_CREATE:
                    if (op_title := op.get('title')) is not None:
                        assert isinstance(op_title, str)
                        title = op_title
                    author = self.resolve_identity(author_id)
                    creator = author
                    created_at = self._format_timestamp(timestamp)
                    if message := op.get('message'):
                        assert isinstance(message, str)
                        files = op.get('files') or []
                        assert isinstance(files, list)
                        assert _is_list(files, str)
                        combined_id = _combine_ids(bid, op_id)
                        cmt = Comment(
                            id=combined_id,
                            author=author,
                            text=message,
                            created_at=self._format_timestamp(timestamp),
                            count=comment_count,
                            attachment_ids=list(files),
                        )
                        comments.append(cmt)
                        op_hash_map[op_id] = cmt
                        comment_count += 1
                    # Collect operation-level metadata
                    op_meta = op.get('metadata')
                    if op_meta is not None:
                        assert isinstance(op_meta, dict)
                        assert _is_dict_values(op_meta, str)
                        metadata.update(op_meta)

                elif op_type == OP_SET_TITLE:
                    if (op_title := op.get('title')) is not None:
                        assert isinstance(op_title, str)
                        title = op_title

                elif op_type == OP_ADD_COMMENT:
                    message = op.get('message', '')
                    assert isinstance(message, str)
                    files = op.get('files') or []
                    assert isinstance(files, list)
                    assert _is_list(files, str)
                    op_author = self.resolve_identity(author_id)
                    combined_id = _combine_ids(bid, op_id)
                    cmt = Comment(
                        id=combined_id,
                        author=op_author,
                        text=message,
                        created_at=self._format_timestamp(timestamp),
                        count=comment_count,
                        attachment_ids=list(files),
                    )
                    comments.append(cmt)
                    op_hash_map[op_id] = cmt
                    comment_count += 1

                elif op_type == OP_SET_STATUS:
                    status_val = op.get('status', STATUS_OPEN)
                    assert isinstance(status_val, int)
                    is_open = status_val == STATUS_OPEN

                elif op_type == OP_LABEL_CHANGE:
                    added = op.get('added') or []
                    assert isinstance(added, list)
                    for lbl in added:
                        assert isinstance(lbl, str)
                        labels.add(lbl)
                    removed = op.get('removed') or []
                    assert isinstance(removed, list)
                    for lbl in removed:
                        assert isinstance(lbl, str)
                        labels.discard(lbl)

                elif op_type == OP_EDIT_COMMENT:
                    target = op.get('target', '')
                    assert isinstance(target, str)
                    new_message = op.get('message', '')
                    assert isinstance(new_message, str)
                    matched = op_hash_map.get(target)
                    if matched is not None:
                        matched.text = new_message
                        new_files = op.get('files') or []
                        assert isinstance(new_files, list)
                        assert _is_list(new_files, str)
                        if new_files:
                            matched.attachment_ids = list(new_files)

                elif op_type == OP_SET_METADATA:
                    new_meta = op.get('new_metadata')
                    if new_meta is not None:
                        assert isinstance(new_meta, dict)
                        assert _is_dict_values(new_meta, str)
                        metadata.update(new_meta)

                elif op_type == OP_NOOP:
                    pass  # No-op, used for bridge metadata

        # Use a fallback identity if somehow no OP_CREATE was found
        if creator is None:
            creator = Identity(id='unknown', name='unknown', email='unknown')
        if created_at is None:
            created_at = datetime.fromtimestamp(0, tz=timezone.utc)

        bug = Bug(
            id=bid,
            title=title,
            status=Status.OPEN if is_open else Status.CLOSED,
            creator=creator,
            created_at=created_at,
            labels=labels,
            comments=comments,
            metadata=metadata,
        )
        self._bug_cache[bid] = bug
        return bug

    def build_bug_summary(
        self,
        bid: str,
        packs: list[JsonObject] | None = None,
    ) -> BugSummary:
        """Build a lightweight bug summary by replaying operation packs.

        Unlike :meth:`build_bug`, this skips op hashing, comment text,
        EditComment replay, metadata, and per-op identity resolution.
        Only the raw *creator_id* string is stored; batch-resolve
        identities separately after collecting summaries.
        """
        bid = self.resolve_bug_id(bid)
        if bid in self._summary_cache:
            return self._summary_cache[bid]

        if packs is None:
            packs, _raw_blobs = self._get_op_packs(bid)
        if not packs:
            raise BugNotFoundError('no operation packs for bug %s' % bid)

        title = ''
        is_open = True
        creator_id = ''
        created_at: datetime | None = None
        labels: set[str] = set()
        comment_count = 0
        latest_ts = 0

        for pack in packs:
            author_value = pack.get('author', {})
            assert isinstance(author_value, dict)
            author_id = author_value.get('id', '')
            assert isinstance(author_id, str)
            ops = pack.get('ops', [])
            assert isinstance(ops, list)
            for op in ops:
                assert isinstance(op, dict)
                op_type = op.get('type', 0)
                assert isinstance(op_type, int)
                ts = op.get('timestamp', 0)
                if isinstance(ts, int) and ts > latest_ts:
                    latest_ts = ts

                if op_type == OP_CREATE:
                    if (op_title := op.get('title')) is not None:
                        assert isinstance(op_title, str)
                        title = op_title
                    creator_id = author_id
                    timestamp = op.get('timestamp', 0)
                    assert isinstance(timestamp, int)
                    created_at = self._format_timestamp(timestamp)
                    if message := op.get('message'):
                        assert isinstance(message, str)
                        comment_count += 1

                elif op_type == OP_SET_TITLE:
                    if (op_title := op.get('title')) is not None:
                        assert isinstance(op_title, str)
                        title = op_title

                elif op_type == OP_ADD_COMMENT:
                    comment_count += 1

                elif op_type == OP_SET_STATUS:
                    status_val = op.get('status', STATUS_OPEN)
                    assert isinstance(status_val, int)
                    is_open = status_val == STATUS_OPEN

                elif op_type == OP_LABEL_CHANGE:
                    added = op.get('added') or []
                    assert isinstance(added, list)
                    for lbl in added:
                        assert isinstance(lbl, str)
                        labels.add(lbl)
                    removed = op.get('removed') or []
                    assert isinstance(removed, list)
                    for lbl in removed:
                        assert isinstance(lbl, str)
                        labels.discard(lbl)

        if created_at is None:
            created_at = datetime.fromtimestamp(0, tz=timezone.utc)

        # Resolve the creator's display name and derive the last-activity
        # time so native summaries carry the same fields as those built
        # from the git-bug CLI cache. Without this, list views that read
        # author_name/edited_at (e.g. right after a pull invalidates the
        # CLI cache) show blank submitters and mis-sort by an epoch date.
        # resolve_identity is cached and cheap with pygit2, and only the
        # creator (one per bug) is resolved -- per-op identity resolution
        # stays deferred to build_bug().
        author_name = ''
        if creator_id:
            author_name = self.resolve_identity(creator_id).name
        edited_at = self._format_timestamp(latest_ts) if latest_ts else created_at

        summary = BugSummary(
            id=bid,
            title=title,
            status=Status.OPEN if is_open else Status.CLOSED,
            creator_id=creator_id,
            created_at=created_at,
            labels=frozenset(labels),
            comment_count=comment_count,
            author_name=author_name,
            edited_at=edited_at,
        )
        self._summary_cache[bid] = summary
        return summary

    def prefetch_bugs(self, bids: list[str]) -> None:
        """Warm the cache by building bugs for the given IDs.

        With pygit2, blob reads are already fast in-process calls,
        so this simply builds each bug and caches the result.
        """
        for bid in bids:
            if bid in self._bug_cache:
                continue
            try:
                self.build_bug(bid)
            except BugNotFoundError:
                continue

    # -- Cache management ----------------------------------------------------

    def invalidate(self, bid: str | None = None) -> None:
        """Clear cached data.

        If *bid* is given, only that bug is evicted. Otherwise all
        caches (bugs, identities, resolve) are cleared.
        """
        if bid:
            try:
                full_bid = self.resolve_bug_id(bid)
            except (BugNotFoundError, AmbiguousBugIdError):
                full_bid = bid
            self._bug_cache.pop(full_bid, None)
            self._summary_cache.pop(full_bid, None)
        else:
            self._bug_cache.clear()
            self._summary_cache.clear()
            self._identity_cache.clear()
            self._resolve_cache.clear()
