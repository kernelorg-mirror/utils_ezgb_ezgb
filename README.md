# ezgb: easy git-bug for Python

A standalone Python library for working with
[git-bug](https://github.com/git-bug/git-bug) repositories. It lets you
list, create, query, and update bugs that are stored as native git
objects -- no external database required.

## Features

- **Fast reads** -- bug data is read directly from git objects via pygit2
  (libgit2 bindings). Listing uses the git-bug CLI cache when available
  (~100ms for hundreds of bugs).
- **Safe writes** -- all mutations go through the `git bug` CLI, which
  keeps Lamport clocks and the operation DAG consistent.
- **Simple API** -- one main class (`GitBugRepo`) with plain Python
  dataclasses for bugs, comments, and identities.

## Quick example

```python
from ezgb import GitBugRepo, Status

repo = GitBugRepo('/path/to/repo')

# List open bugs
for bug in repo.list_bugs(status=Status.OPEN):
    print(bug.title)

# Create a new bug
bug = repo.create_bug('Login page is broken', 'Steps to reproduce...')

# Add a comment
repo.add_comment(bug.id, 'I can reproduce this on Firefox 130.')
```

## Requirements

- Python 3.10 or later
- pygit2 (libgit2 Python bindings)
- `git-bug` v0.10+ on your `$PATH` (needed for write operations and
  fast cached listing)

## Installation

```bash
git clone https://git.kernel.org/pub/scm/utils/ezgb/ezgb.git
cd ezgb
pip install -e .
```

## Documentation

Full documentation is in the `docs/` directory. Build it with:

```bash
pip install sphinx sphinx-rtd-theme
make -C docs html
```

Then open `docs/_build/html/index.html` in your browser.

## Development checks

```bash
uv sync --all-groups
./ci.sh
```

The CI script runs Ruff, ty, mypy, pyright, and pytest. The test suite
includes unit tests (using real git objects in temporary repos via
pygit2) and integration tests that exercise the full stack including the
`git-bug` binary.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on submitting
patches, coding style, and the Developer Certificate of Origin.

## License

GPLv2 or later. See [COPYING](COPYING) for the full text.
