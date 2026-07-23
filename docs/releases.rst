Release notes
=============

A high-level overview of what changed in each ezgb release. For the
full, itemised list -- including development and packaging changes --
see the ``CHANGELOG.rst`` file at the root of the source tree.

.. contents:: On this page
   :local:
   :depth: 1

0.2.0 (2026-07-23)
------------------

Bug summaries gained consistent author and activity metadata. Native
summaries built by :meth:`~ezgb.GitBugRepo.list_bug_summaries` (the
pygit2 read path) now populate ``author_name`` with the creator's
resolved display name and ``edited_at`` with the latest operation
timestamp, matching the summaries produced from the git-bug CLI cache.
Previously the native path left these blank and at the Unix epoch, which
was most noticeable right after a ``pull`` -- tools that list bugs, such
as b4's bug TUI, showed empty submitters and sorted by an epoch date.

The read-only Lua library likewise now records ``edited_at`` in its bug
summaries, so Lua consumers can sort by last activity.

Development-wise, ezgb picked up a full CI guard suite -- code
formatting, linting, three type checkers (mypy, pyright, and ty), and a
test matrix spanning every supported interpreter (Python 3.9--3.14) --
together with a fully type-annotated test suite and a pinned
``uv.lock`` for reproducible installs.

0.1.1 and earlier
-----------------

The initial development releases established the foundations: a Python
read/write library and a read-only Lua port, native git-object reads via
pygit2 (and luagit2 in Lua), the fast git-bug CLI-cache listing path,
and the core bug, comment, and identity model. See the git history for
the details of these early releases.
