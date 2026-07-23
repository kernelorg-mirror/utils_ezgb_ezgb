ezgb: easy git-bug
===================

**ezgb** is a standalone library for working with
`git-bug <https://github.com/git-bug/git-bug>`_ repositories. It lets
you list, create, query, and update bugs that are stored as native git
objects -- no external database required.

Both Python and Lua implementations are provided. The Python library
supports reading and writing; the Lua library is read-only and is
designed for embedding in tools like `cgit <https://git.zx2c4.com/cgit/>`_.

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

.. code-block:: lua

   local ezgb = require("ezgb")
   ezgb.open("/path/to/repo")

   for _, ref in ipairs(ezgb.list_bug_refs()) do
       local bug = ezgb.build_bug(ref.id)
       print(bug.title)
   end

Key features
------------

- **Fast reads** -- bug data is read directly from git objects (via
  pygit2 in Python, via luagit2 in Lua), so listing hundreds of
  bugs is quick.
- **Safe writes** (Python only) -- all mutations go through the
  ``git bug`` CLI, which keeps Lamport clocks and the operation DAG
  consistent.
- **Simple API** -- one main class in Python (``GitBugRepo``), one
  module in Lua (``ezgb``), with plain data structures for bugs,
  comments, and identities.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   quickstart
   api
   architecture
   releases
