# ezgb: easy git-bug for Python

A standalone Python library for working with
[git-bug](https://github.com/git-bug/git-bug) repositories. It lets you
list, create, query, and update bugs that are stored as native git
objects -- no external database required.

## Features

- **Fast reads** -- bug data is read directly from git objects using
  `git cat-file --batch`, so listing hundreds of bugs is quick.
- **Safe writes** -- all mutations go through the `git bug` CLI, which
  keeps Lamport clocks and the operation DAG consistent.
- **Simple API** -- one main class (`GitBugRepo`) with plain Python
  dataclasses for bugs, comments, and identities.
- **No dependencies** -- only the Python standard library and a working
  `git` installation are needed at runtime.

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

- Python 3.9 or later
- `git` on your `$PATH`
- `git-bug` v0.10+ on your `$PATH` (only needed for write operations)

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

## Running tests

```bash
pip install -e '.[dev]'
python -m pytest
```

The test suite includes both unit tests (with mocked git) and integration
tests that exercise the real `git-bug` binary against ephemeral
repositories.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on submitting
patches, coding style, and the Developer Certificate of Origin.

## License

GPLv2 or later. See [COPYING](COPYING) for the full text.
