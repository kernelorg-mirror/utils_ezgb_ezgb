=========
Changelog
=========

All notable changes to ezgb are documented in this file. This project
adheres to `Semantic Versioning <https://semver.org/>`_.

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
