=========
Changelog
=========

All notable changes to ezgb are documented in this file. This project
adheres to `Semantic Versioning <https://semver.org/>`_.

0.2.1 (2026-10-06)
==================

Changed
-------

- The minimum supported Python version is now 3.10 (was 3.9). Python
  3.9 reached end of life in October 2025, and the code now uses
  ``typing.TypeGuard``, which was added in 3.10. On Python 3.9, pip
  keeps installing 0.2.0.
- Values decoded from git-bug JSON are now checked before use. Bug,
  operation and identity data with unexpected types fails early, at
  the point where it is read.
- The git helpers now use ``subprocess.run()`` instead of
  ``subprocess.Popen``. There is no change in behaviour.

Fixed
-----

- An operation with a ``null`` title no longer sets the bug title to
  ``None``. The previous title is kept instead.
- ``COPYING`` now has the current Free Software Foundation address.

Development
-----------

- Enabled strict pyright checks for the source code, without the
  earlier blanket exemptions for unknown types and private names.
  Decoded JSON now has precise ``JsonValue`` and ``JsonObject`` types
  and git objects are narrowed to their pygit2 classes.
- Added the typed ``json_loads()`` and ``json_raw_decode()`` helpers,
  so the newer ty "unsound assignment" checks pass without
  suppressions.
- Replaced the dynamic ``BugWriter`` test double with a typed subclass.
- The ``ci-matrix.sh`` interpreter matrix now covers Python 3.10--3.14.
- Documented ``uv sync`` and ``ci.sh`` as the way to run local checks,
  in the README and the contributor guide.
- Updated the development tools in ``uv.lock``: pyright 1.1.414,
  ruff 0.16.10, mypy 2.4.0 and ty 0.0.84.

0.2.0 (2026-07-23)
==================

Fixed
-----

- Native bug summaries (``BugReader.build_bug_summary``) now resolve the
  creator's display name into ``author_name`` and track the latest
  operation timestamp in ``edited_at``, so they match the values
  produced by the git-bug CLI cache. Previously the native read path
  left ``author_name`` empty and ``edited_at`` at the Unix epoch. This
  was most visible right after a ``pull`` -- which invalidates the CLI
  cache and forces the native fallback -- where consumers such as b4's
  bug TUI showed blank submitters and sorted the list by an epoch
  "last activity" date. Only the creator identity (one per bug) is
  resolved here; per-operation identity resolution stays deferred to
  ``build_bug()``.

Added
-----

- The read-only Lua library now records ``edited_at`` (the latest
  timestamp across all operation packs) in its bug summaries, letting
  Lua consumers sort bugs by last activity instead of creation date.

Development
-----------

- Added ``ci.sh`` and ``ci-matrix.sh`` CI guard scripts. ``ci.sh`` runs
  formatting, linting, three type checkers (mypy, pyright, ty), and the
  test suite; ``ci-matrix.sh`` runs an import smoke check and the suite
  under every supported interpreter (3.9--3.14) via uv-managed Pythons.
- Added ``pyright`` and ``ty`` to the dev dependency group and
  configured ruff-format, pyright, and ty in ``pyproject.toml``.
- Fully type-annotated the test suite and resolved the remaining pyright
  and ty findings in ``src/``.
- Reformatted the code base with ``ruff format`` and dropped unused
  asyncio pytest options.
- Committed ``uv.lock`` for reproducible CI installs.

0.1.1
=====

- Initial changelog baseline. See the git history for changes in this
  and earlier releases.
