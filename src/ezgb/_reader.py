#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 by the Linux Foundation
"""Git object reading, operation pack replay, and caching for ezgb."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ezgb._git import git_lines, git_run
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
    Comment,
    Identity,
    Status,
    UnsupportedFormatError,
)

logger = logging.getLogger('ezgb')

# Length of a full git-bug entity ID (SHA-256 hex)
_ID_LENGTH = 64


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
        self._bug_cache: dict[str, Bug] = {}
        self._identity_cache: dict[str, Identity] = {}
        self._resolve_cache: dict[str, str] = {}

    # -- Log parsing ---------------------------------------------------------

    @staticmethod
    def _parse_log_raw(
        output: str, target_file: str = 'ops',
    ) -> list[tuple[str, str]]:
        """Parse ``git log --raw --full-index --format=%H`` output.

        Returns ``[(commit_hash, blob_hash)]`` for each commit whose
        raw diff touches a file named *target_file*.
        """
        entries: list[tuple[str, str]] = []
        current_commit: str | None = None
        for line in output.splitlines():
            line = line.rstrip()
            if not line:
                continue
            # 64-hex-char line -> commit hash
            if len(line) == 40 and all(c in '0123456789abcdef' for c in line):
                current_commit = line
                continue
            # Raw diff: :old_mode new_mode old_hash new_hash status\tfilename
            if line.startswith(':') and current_commit:
                parts = line.split('\t', 1)
                if len(parts) != 2:
                    continue
                filename = parts[1]
                if filename != target_file:
                    continue
                diff_info = parts[0].split()
                if len(diff_info) >= 4:
                    new_blob = diff_info[3]
                    entries.append((current_commit, new_blob))
        return entries

    # -- Blob reading --------------------------------------------------------

    def _batch_cat_blobs(self, hashes: list[str]) -> dict[str, str]:
        """Read multiple blobs in one ``git cat-file --batch`` call.

        Returns ``{input_hash: content_string}``. Missing objects are
        skipped.
        """
        if not hashes:
            return {}
        stdin = ('\n'.join(hashes) + '\n').encode()
        ecode, raw = git_run(
            self._repo, ['cat-file', '--batch'],
            stdin=stdin, decode=False,
        )
        if ecode != 0:
            return {}
        data: bytes = raw if isinstance(raw, bytes) else raw.encode()
        result: dict[str, str] = {}
        pos = 0
        hash_iter = iter(hashes)
        while pos < len(data):
            nl = data.find(b'\n', pos)
            if nl < 0:
                break
            header = data[pos:nl].decode()
            input_hash = next(hash_iter, None)
            if input_hash is None:
                break
            if header.endswith(' missing'):
                pos = nl + 1
                continue
            hdr_parts = header.split()
            if len(hdr_parts) < 3:
                pos = nl + 1
                continue
            try:
                size = int(hdr_parts[2])
            except ValueError:
                pos = nl + 1
                continue
            content_start = nl + 1
            content_end = content_start + size
            if content_end > len(data):
                break
            result[input_hash] = data[content_start:content_end].decode()
            pos = content_end + 1
        return result

    def _cat_blob(self, blob_hash: str) -> str:
        """Read a single git blob as text."""
        ecode, out = git_run(self._repo, ['cat-file', 'blob', blob_hash])
        if ecode != 0 or not isinstance(out, str):
            raise BugNotFoundError('failed to read blob %s' % blob_hash)
        return out

    def cat_blob_bytes(self, blob_hash: str) -> bytes:
        """Read a single git blob as raw bytes.

        Useful for reading file attachments referenced by
        :attr:`Comment.attachment_ids`.
        """
        ecode, out = git_run(
            self._repo, ['cat-file', 'blob', blob_hash], decode=False,
        )
        if ecode != 0 or not isinstance(out, bytes):
            raise BugNotFoundError('failed to read blob %s' % blob_hash)
        return out

    # -- Format version checking ---------------------------------------------

    @staticmethod
    def _check_format_version(
        output: str, prefix: str, supported: int,
    ) -> None:
        """Validate the git-bug format version from git log --raw output.

        Scans for tree entries matching ``{prefix}{N}`` (e.g.
        ``version-4``) and raises UnsupportedFormatError if the
        version does not match.
        """
        for line in output.splitlines():
            if not line.startswith(':'):
                continue
            parts = line.split('\t', 1)
            if len(parts) != 2:
                continue
            filename = parts[1]
            if filename.startswith(prefix):
                try:
                    version = int(filename[len(prefix):])
                except ValueError:
                    continue
                if version != supported:
                    raise UnsupportedFormatError(
                        'unsupported git-bug format: %s%d (expected %s%d)'
                        % (prefix, version, prefix, supported)
                    )
                return

    # -- Ref enumeration -----------------------------------------------------

    def list_bug_refs(
        self, *, since: int = 0,
    ) -> list[tuple[str, str]]:
        """Return ``[(bug_id, commit_hash)]`` for all bugs.

        If *since* is a positive unix timestamp, only refs whose
        tip commit is newer than that timestamp are returned.
        """
        if since > 0:
            fmt = '%(refname:short) %(objectname) %(committerdate:unix)'
        else:
            fmt = '%(refname:short) %(objectname)'
        lines = git_lines(self._repo, [
            'for-each-ref', '--format=%s' % fmt,
            'refs/bugs/',
        ])
        results: list[tuple[str, str]] = []
        for line in lines:
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            refname, commit = parts[0], parts[1]
            if since > 0 and len(parts) == 3:
                try:
                    ts = int(parts[2])
                except ValueError:
                    continue
                if ts < since:
                    continue
            bid = refname.split('/')[-1]
            results.append((bid, commit))
        return results

    def list_identity_refs(self) -> list[tuple[str, str]]:
        """Return ``[(identity_id, commit_hash)]`` for all identities."""
        lines = git_lines(self._repo, [
            'for-each-ref', '--format=%(refname:short) %(objectname)',
            'refs/identities/',
        ])
        results: list[tuple[str, str]] = []
        for line in lines:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            refname, commit = parts
            iid = refname.split('/')[-1]
            results.append((iid, commit))
        return results

    # -- Bug ID resolution ---------------------------------------------------

    def resolve_bug_id(self, bid: str) -> str:
        """Resolve a possibly-abbreviated bug ID to the full ref name.

        Raises AmbiguousBugIdError when more than one ref matches.
        Raises BugNotFoundError when no ref matches.
        """
        if bid in self._resolve_cache:
            return self._resolve_cache[bid]
        lines = git_lines(self._repo, [
            'for-each-ref', '--format=%(refname)',
            'refs/bugs/' + bid + '*',
        ])
        candidates = [
            ln.strip().rsplit('/', 1)[-1] for ln in lines if ln.strip()
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

    def _get_op_packs(self, bid: str) -> list[dict[str, Any]]:
        """Walk the commit chain for a bug and return operation packs,
        oldest first.
        """
        bid = self.resolve_bug_id(bid)
        ecode, output = git_run(self._repo, [
            'log', '--raw', '--full-index', '--format=%H',
            '--reverse', 'refs/bugs/' + bid,
        ])
        if ecode != 0 or not isinstance(output, str):
            return []
        self._check_format_version(output, 'version-', SUPPORTED_BUG_FORMAT)
        entries = self._parse_log_raw(output)
        if not entries:
            return []
        blob_hashes = [h for _, h in entries]
        blobs = self._batch_cat_blobs(blob_hashes)
        packs: list[dict[str, Any]] = []
        for _commit, blob_hash in entries:
            raw = blobs.get(blob_hash)
            if raw is None:
                continue
            try:
                pack: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    'failed to parse ops blob %s for bug %s',
                    blob_hash, bid,
                )
                continue
            packs.append(pack)
        return packs

    # -- Identity resolution -------------------------------------------------

    def resolve_identity(self, identity_id: str) -> Identity:
        """Resolve an identity ID to an Identity dataclass.

        Reads the most recent version blob from the identity's commit
        chain. Identity versions use a flat JSON format (not op-packs).
        """
        if identity_id in self._identity_cache:
            return self._identity_cache[identity_id]

        # Get the latest commit's version blob
        ecode, output = git_run(self._repo, [
            'log', '--raw', '--full-index', '--format=%H',
            '-1', 'refs/identities/' + identity_id,
        ])
        if ecode != 0 or not isinstance(output, str) or not output.strip():
            fallback = Identity(
                id=identity_id, name=identity_id, email=identity_id,
            )
            self._identity_cache[identity_id] = fallback
            return fallback

        # Identity commits store a blob named 'version' (not 'ops')
        entries = self._parse_log_raw(output, target_file='version')
        if not entries:
            fallback = Identity(
                id=identity_id, name=identity_id, email=identity_id,
            )
            self._identity_cache[identity_id] = fallback
            return fallback

        _commit, version_hash = entries[0]
        raw = self._cat_blob(version_hash)
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            fallback = Identity(
                id=identity_id, name=identity_id, email=identity_id,
            )
            self._identity_cache[identity_id] = fallback
            return fallback

        # Validate format version
        fmt_version = data.get('version')
        if fmt_version is not None and fmt_version != SUPPORTED_IDENTITY_FORMAT:
            raise UnsupportedFormatError(
                'unsupported identity format: %d (expected %d)'
                % (fmt_version, SUPPORTED_IDENTITY_FORMAT)
            )

        identity = Identity(
            id=identity_id,
            name=data.get('name', identity_id),
            email=data.get('email', identity_id),
            login=data.get('login', ''),
        )
        self._identity_cache[identity_id] = identity
        return identity

    # -- Bug snapshot building -----------------------------------------------

    @staticmethod
    def _op_hash(op: dict[str, Any]) -> str:
        """Compute a git-bug compatible operation ID.

        git-bug derives IDs as SHA256 of the compact JSON serialization.
        """
        raw = json.dumps(op, separators=(',', ':')).encode()
        return hashlib.sha256(raw).hexdigest()

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
        self, bid: str,
        packs: list[dict[str, Any]] | None = None,
    ) -> Bug:
        """Reconstruct a bug snapshot by replaying operation packs.

        Returns a Bug dataclass. Results are cached by full bug ID.
        """
        bid = self.resolve_bug_id(bid)
        if bid in self._bug_cache:
            return self._bug_cache[bid]

        if packs is None:
            packs = self._get_op_packs(bid)
        if not packs:
            raise BugNotFoundError('no operation packs for bug %s' % bid)

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

        for pack in packs:
            author_id = pack.get('author', {}).get('id', '')
            for op in pack.get('ops', []):
                op_type = op.get('type', 0)
                timestamp = op.get('timestamp', 0)
                op_id = self._op_hash(op)

                if op_type == OP_CREATE:
                    title = op.get('title', '')
                    author = self.resolve_identity(author_id)
                    creator = author
                    created_at = self._format_timestamp(timestamp)
                    message = op.get('message', '')
                    if message:
                        files = op.get('files') or []
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
                    if op_meta:
                        metadata.update(op_meta)

                elif op_type == OP_SET_TITLE:
                    title = op.get('title', title)

                elif op_type == OP_ADD_COMMENT:
                    message = op.get('message', '')
                    files = op.get('files') or []
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
                    is_open = (status_val == STATUS_OPEN)

                elif op_type == OP_LABEL_CHANGE:
                    for lbl in op.get('added') or []:
                        labels.add(lbl)
                    for lbl in op.get('removed') or []:
                        labels.discard(lbl)

                elif op_type == OP_EDIT_COMMENT:
                    target = op.get('target', '')
                    new_message = op.get('message', '')
                    matched = op_hash_map.get(target)
                    if matched is not None:
                        matched.text = new_message
                        new_files = op.get('files') or []
                        if new_files:
                            matched.attachment_ids = list(new_files)

                elif op_type == OP_SET_METADATA:
                    new_meta = op.get('new_metadata')
                    if new_meta:
                        metadata.update(new_meta)

                elif op_type == OP_NOOP:
                    pass  # No-op, used for bridge metadata

        # Synthesise assigned_to from assigned:USER label
        assigned_to = ''
        for lbl in labels:
            if lbl.startswith('assigned:'):
                assigned_to = lbl[len('assigned:'):]
                break

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
            assigned_to=assigned_to,
            metadata=metadata,
        )
        self._bug_cache[bid] = bug
        return bug

    def prefetch_bugs(self, bids: list[str]) -> None:
        """Warm the cache for multiple bugs using batched blob reads.

        Collects ops blob hashes for all uncached bugs, then reads
        them all in a single ``git cat-file --batch`` call.
        """
        bug_entries: dict[str, list[tuple[str, str]]] = {}
        all_hashes: list[str] = []
        for bid in bids:
            if bid in self._bug_cache:
                continue
            ecode, output = git_run(self._repo, [
                'log', '--raw', '--full-index', '--format=%H',
                '--reverse', 'refs/bugs/' + bid,
            ])
            if ecode != 0 or not isinstance(output, str):
                continue
            entries = self._parse_log_raw(output)
            if entries:
                bug_entries[bid] = entries
                all_hashes.extend(h for _, h in entries)

        if not all_hashes:
            return

        blobs = self._batch_cat_blobs(all_hashes)

        for bid, entries in bug_entries.items():
            packs: list[dict[str, Any]] = []
            for _commit, blob_hash in entries:
                raw = blobs.get(blob_hash)
                if raw is None:
                    continue
                try:
                    pack: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(
                        'failed to parse ops blob %s for bug %s',
                        blob_hash, bid,
                    )
                    continue
                packs.append(pack)
            if packs:
                try:
                    self.build_bug(bid, packs=packs)
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
        else:
            self._bug_cache.clear()
            self._identity_cache.clear()
            self._resolve_cache.clear()
