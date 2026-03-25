ezgb: easy git-bug for Python
==============================

**ezgb** is a standalone Python library for working with
`git-bug <https://github.com/git-bug/git-bug>`_ repositories. It lets
you list, create, query, and update bugs that are stored as native git
objects -- no external database required.

.. code-block:: python

   from ezgb import GitBugRepo, Status

   repo = GitBugRepo('/path/to/repo')

   # List open bugs
   for bug in repo.list_bugs(status=Status.OPEN):
       print(bug.title)

   # Create a new bug
   bug = repo.create_bug('Login page is broken', 'Steps to reproduce...')

   # Add a comment
   repo.add_comment(bug.id, 'I can reproduce this on Firefox 130.')

Key features
------------

- **Fast reads** -- bug data is read directly from git objects using
  ``git cat-file --batch``, so listing hundreds of bugs is quick.
- **Safe writes** -- all mutations go through the ``git bug`` CLI, which
  keeps Lamport clocks and the operation DAG consistent.
- **Simple API** -- one main class (``GitBugRepo``) with plain Python
  dataclasses for bugs, comments, and identities.
- **No dependencies** -- only the Python standard library and a working
  ``git`` installation are needed at runtime.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   quickstart
   api
   architecture
