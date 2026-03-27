Quick start
===========

This page walks through the most common tasks. Every example assumes
you already have a git repository with git-bug set up.

.. contents:: On this page
   :local:
   :depth: 2

Opening a repository
--------------------

Point ``GitBugRepo`` at any git working tree or bare repository that
contains git-bug data:

.. code-block:: python

   from ezgb import GitBugRepo

   repo = GitBugRepo('/srv/git/myproject.git')

Listing bugs
------------

Call :meth:`~ezgb.GitBugRepo.list_bugs` to get every bug, or pass
filters:

.. code-block:: python

   from ezgb import Status

   # All bugs
   all_bugs = repo.list_bugs()

   # Only open bugs
   open_bugs = repo.list_bugs(status=Status.OPEN)

   # Only bugs with a specific label
   net_bugs = repo.list_bugs(label='area/network')

   # Only bugs modified after a given time
   recent = repo.list_bugs(since='2024-11-01 00:00:00')

Each item in the list is a :class:`~ezgb.Bug` dataclass.

The *since* parameter accepts a unix timestamp (``int``), a
:class:`~datetime.datetime`, or a date string in ISO-8601 /
``YYYYMMDDHHMMSS`` format.

Iterating lazily
----------------

For large repositories, :meth:`~ezgb.GitBugRepo.iter_bugs` yields
bugs one at a time instead of loading them all into memory:

.. code-block:: python

   for bug in repo.iter_bugs(status=Status.OPEN):
       print(bug.title)

It accepts the same filters as ``list_bugs``.

Reading a single bug
--------------------

If you know the bug ID (or an unambiguous prefix), use
:meth:`~ezgb.GitBugRepo.get_bug`:

.. code-block:: python

   bug = repo.get_bug('415b4497')

   print(bug.title)          # 'Login page is broken'
   print(bug.status)         # Status.OPEN
   print(bug.creator.name)   # 'Alice'
   print(bug.labels)         # {'area/web', 'priority/high'}

   for comment in bug.comments:
       print(comment.author.name, comment.text)

Creating a bug
--------------

:meth:`~ezgb.GitBugRepo.create_bug` returns the new bug as a
:class:`~ezgb.Bug`:

.. code-block:: python

   bug = repo.create_bug(
       title='Crash on startup',
       body='The application crashes when the config file is missing.',
   )
   print(bug.id)   # full 64-character hex ID

.. note::

   Write operations need the ``git bug`` CLI on your ``$PATH``.

Adding comments
---------------

.. code-block:: python

   comment = repo.add_comment(bug.id, 'I can reproduce this on Fedora 41.')
   print(comment.count)  # 1 (the first comment after the description)

Changing status
---------------

.. code-block:: python

   repo.set_status(bug.id, Status.CLOSED)

   # Later, reopen it:
   repo.set_status(bug.id, Status.OPEN)

Editing the title
-----------------

.. code-block:: python

   repo.set_title(bug.id, 'Crash on startup when config is missing')

Working with labels
-------------------

.. code-block:: python

   repo.add_label(bug.id, 'priority/high')
   repo.remove_label(bug.id, 'priority/high')

Removing a bug
--------------

To permanently delete a bug (this is irreversible):

.. code-block:: python

   repo.remove_bug(bug.id)

Syncing with a remote
---------------------

Push and pull git-bug data to/from a remote:

.. code-block:: python

   # Push bugs and identities
   returncode, stdout, stderr = repo.push('origin')

   # Pull and merge (caches are invalidated automatically)
   returncode, stdout, stderr = repo.pull('origin')

Reading attachments
-------------------

Comments can reference file attachments by blob hash. Use
:meth:`~ezgb.GitBugRepo.get_attachment` to read the raw bytes:

.. code-block:: python

   for comment in bug.comments:
       for blob_hash in comment.attachment_ids:
           data = repo.get_attachment(blob_hash)
           print(len(data), 'bytes')

Listing identities
------------------

Every person who has interacted with git-bug in the repository has an
identity:

.. code-block:: python

   for identity in repo.list_identities():
       print(identity.name, identity.email)

Caching
-------

ezgb caches bug snapshots in memory after the first read. If you know
the repository has changed (for example after a ``git pull``), clear the
cache:

.. code-block:: python

   # Clear everything
   repo.invalidate()

   # Clear just one bug
   repo.invalidate(bug.id)
