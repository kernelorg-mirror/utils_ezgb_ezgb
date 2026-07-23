Architecture
============

This page explains how ezgb works under the hood. You do not need to
read this to use the library, but it may help if you want to contribute
or if you are debugging unexpected behaviour.

.. contents:: On this page
   :local:
   :depth: 2

Design principle: read fast, write safe
---------------------------------------

ezgb splits its work into two paths:

- **Reads** go directly to git objects via pygit2 (libgit2 bindings).
  This avoids spawning subprocesses for every lookup and makes bulk
  operations fast.
- **Listing** uses the git-bug CLI excerpt cache when available
  (~100ms for hundreds of bugs). Falls back to native reads when the
  CLI is not installed.
- **Writes** go through the ``git bug`` CLI. The CLI maintains Lamport
  clocks and the operation DAG, so bypassing it would risk corrupting
  the data.

Module layout
-------------

Python::

   src/ezgb/
     __init__.py   GitBugRepo facade and public API
     _models.py    Dataclasses, enums, exceptions, constants
     _git.py       Subprocess wrapper for git-bug CLI (write ops)
     _reader.py    BugReader: reads and caches bug data via pygit2
     _writer.py    BugWriter: delegates mutations to the git-bug CLI

Lua::

   lua/
     ezgb.lua      Read-only library (ref enumeration, op-pack replay,
                    identity resolution, combined ID generation)

How git-bug stores data
-----------------------

git-bug keeps all its data inside the git repository itself, under
special ref namespaces:

- ``refs/bugs/<id>`` -- one ref per bug, where ``<id>`` is a 64-character
  SHA-256 hex string.
- ``refs/identities/<id>`` -- one ref per user identity.

Each ref points to a chain of commits. Every commit in the chain
contains a tree with two entries:

- ``ops`` -- a JSON blob holding an *operation pack* (a list of
  operations by one author).
- ``version-N`` -- an empty blob that records the format version
  (currently ``version-4`` for bugs).

Identity commits use the same structure, but the blob is called
``version`` (a flat JSON object, format version 2).

Operation types
^^^^^^^^^^^^^^^

Bugs are built by replaying operations in order. Each operation has an
integer ``type`` field:

====  ====================  ===========================================
Type  Name                  What it does
====  ====================  ===========================================
1     ``OP_CREATE``         Creates the bug with a title and message
2     ``OP_SET_TITLE``      Changes the title
3     ``OP_ADD_COMMENT``    Adds a new comment
4     ``OP_SET_STATUS``     Sets the status to open (1) or closed (2)
5     ``OP_LABEL_CHANGE``   Adds or removes labels
6     ``OP_EDIT_COMMENT``   Edits an existing comment's text
7     ``OP_NOOP``           No-op, used for bridge metadata
8     ``OP_SET_METADATA``   Attaches key-value metadata
====  ====================  ===========================================

How reading works
-----------------

When you call ``repo.get_bug(bid)``, the following happens:

1. **Resolve the ID.** If ``bid`` is a short prefix, ezgb iterates
   ``refs/bugs/`` references via pygit2 to find the matching full ID.

2. **Walk the commit chain.** pygit2 follows the commit parent chain
   from the ref tip back to the root, collecting the ``ops`` blob from
   each commit's tree (oldest first).

3. **Read the blobs.** Each ``ops`` blob is read directly from the
   repository's object database via pygit2 -- no subprocess overhead.

4. **Replay the operations.** Each operation pack is parsed as JSON
   and its operations are applied in sequence to build a
   :class:`~ezgb.Bug` snapshot.

5. **Cache the result.** The finished ``Bug`` object is stored in an
   in-memory dict, keyed by the full bug ID. Subsequent reads for the
   same bug return the cached object immediately.

Identity resolution follows a similar pattern: read the latest
``version`` blob from the identity's commit chain and parse it.

Fast listing with the CLI cache
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`~ezgb.GitBugRepo.list_bug_summaries` transparently uses the
git-bug CLI's pre-built excerpt cache when available. This runs
``git bug -f json`` which reads the gob-encoded cache at
``.git/git-bug/cache/bugs`` and returns all bug metadata in ~100ms,
regardless of repository size. The method falls back to native pygit2
reads when the CLI is not installed or when a ``since`` filter is used.

How writing works
-----------------

All write methods go through the same path:

1. Run ``git -C <repo> bug <subcommand> ...`` via ``subprocess``.
2. Check the exit code. If non-zero, raise :class:`~ezgb.CliError`.
3. Invalidate the reader's cache for the affected bug (or all bugs, in
   the case of ``create_bug``).
4. Re-read the bug from git objects (via pygit2) so the caller gets an
   up-to-date snapshot.

This means the ``git-bug`` binary must be installed for any write
operation.

Caching
-------

Four in-memory caches are maintained:

- **Bug cache** -- maps full bug ID to a ``Bug`` object.
- **Summary cache** -- maps full bug ID to a ``BugSummary`` object.
- **Identity cache** -- maps identity ID to an ``Identity`` object.
- **Resolve cache** -- maps bug ID prefixes (and full IDs) to full IDs.

Call :meth:`~ezgb.GitBugRepo.invalidate` to clear them. Passing a
specific bug ID only evicts that bug from the bug and summary caches.
Passing no argument clears all four caches.

Write operations automatically invalidate the affected bug, so you
normally do not need to call ``invalidate()`` yourself. The main use
case is when the repository changes externally (for example after a
``git fetch`` or ``git pull``).

Comment IDs
-----------

git-bug identifies comments using *combined IDs* -- a 64-character
string formed by interleaving the bug ID and the operation hash. This
gives each comment a globally unique, deterministic identifier.

The interleaving pattern places the operation hash characters at
positions 1, 3, 5, 9, and then every position where
``i >= 10 and i % 5 == 4``. All other positions come from the bug ID.

Operation hashing
^^^^^^^^^^^^^^^^^

git-bug computes each operation's hash by SHA-256-hashing the compact
JSON serialization produced by Go's ``json.Marshal``. Go escapes
``<``, ``>``, and ``&`` as ``\u003c``, ``\u003e``, ``\u0026`` -- a
Go-specific behaviour that neither Python's ``json.dumps`` nor Lua's
cjson reproduces.

Re-serializing a parsed operation would produce different bytes and a
different hash, which breaks ``OP_EDIT_COMMENT`` matching (the
``target`` field carries the Go-computed hash). To get correct hashes,
both the Python and Lua implementations extract each operation's raw
JSON string directly from the blob, preserving Go's escaping verbatim.

In Python this is done using ``json.JSONDecoder.raw_decode()`` to find
object boundaries in the original string. In Lua, a manual brace-depth
tracker serves the same purpose.

Lua library
-----------

The Lua library (``lua/ezgb.lua``) mirrors the Python reader's
functionality but is read-only -- it has no write path.

Instead of spawning ``git`` subprocesses, it uses
`luagit2 <https://github.com/libgit2/luagit2>`_ (libgit2 bindings)
for direct git object access. This makes it suitable for embedding in
tools like cgit where subprocess overhead would be unacceptable.

The public API returns plain Lua tables:

.. code-block:: lua

   local ezgb = require("ezgb")
   ezgb.open("/path/to/repo")

   -- List bugs
   local refs = ezgb.list_bug_refs()

   -- Build a full bug snapshot
   local bug = ezgb.build_bug(refs[1].id)
   -- bug.title, bug.status, bug.creator, bug.comments, ...

   -- Resolve identities
   local id = ezgb.resolve_identity(identity_id)
   -- id.name, id.email, id.login

Labels are stored as set-tables (``{["label"] = true, ...}``) rather
than arrays. Timestamps are plain unix integers. Error handling uses
Lua's idiomatic ``nil, error_string`` return pattern.
