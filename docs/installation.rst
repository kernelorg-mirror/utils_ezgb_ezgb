Installation
============

Requirements
------------

- **Python 3.9** or later.
- **git** must be on your ``$PATH``.
- **git-bug** (v0.10 or later) must be on your ``$PATH`` if you want to
  create or modify bugs. Reading existing bugs only needs ``git``.

Install from source
-------------------

Clone the repository and install in editable mode:

.. code-block:: bash

   git clone https://git.kernel.org/pub/scm/utils/ezgb/ezgb.git
   cd ezgb
   pip install -e .

To also install the development tools (pytest, mypy, ruff):

.. code-block:: bash

   pip install -e '.[dev]'

Verify
------

Open a Python shell and check that the import works:

.. code-block:: python

   >>> import ezgb
   >>> ezgb.GitBugRepo
   <class 'ezgb.GitBugRepo'>

If you plan to create or modify bugs, make sure ``git-bug`` is
available:

.. code-block:: bash

   $ git bug version
   git-bug version: v0.10.1
