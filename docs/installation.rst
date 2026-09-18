Installation
============

.. contents:: On this page
   :local:
   :depth: 2

Python library
--------------

Requirements
^^^^^^^^^^^^

- **Python 3.10** or later.
- **pygit2** -- Python bindings for libgit2 (``pip install pygit2``).
- **git-bug** (v0.10 or later) must be on your ``$PATH`` if you want to
  create or modify bugs, or to use the fast CLI-cache listing path.
  Reading existing bugs only needs pygit2.

Install from source
^^^^^^^^^^^^^^^^^^^

Clone the repository and install in editable mode:

.. code-block:: bash

   git clone https://git.kernel.org/pub/scm/utils/ezgb/ezgb.git
   cd ezgb
   pip install -e .

To also install the development tools (pytest, mypy, ruff, pyright, and
ty), which live in a PEP 735 ``[dependency-groups]`` table:

.. code-block:: bash

   # with uv (installs the project and the dev tools)
   uv sync

   # or with pip 25.1 or later
   pip install -e . --group dev

Verify
^^^^^^

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

Lua library
-----------

The Lua library is read-only and uses luagit2 for git object access.

Requirements
^^^^^^^^^^^^

- **Lua 5.1** or later (including LuaJIT).
- **luagit2** (``lua-git2``) -- libgit2 bindings for Lua.
- **luaossl** -- OpenSSL bindings (for SHA-256 operation hashing).
- **lua-cjson** or **lua-json** -- JSON parsing.

Install with luarocks
^^^^^^^^^^^^^^^^^^^^^

From the ezgb source tree:

.. code-block:: bash

   cd ezgb
   luarocks make lua-ezgb-scm-1.rockspec

This installs ``ezgb.lua`` to the standard Lua module path (typically
``/usr/share/lua/5.4/`` or ``~/.luarocks/share/lua/5.4/``).

Verify
^^^^^^

.. code-block:: console

   $ lua -e 'local ezgb = require("ezgb"); print("ok")'
   ok
