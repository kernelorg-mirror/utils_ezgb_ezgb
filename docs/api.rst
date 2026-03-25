API reference
=============

.. contents:: On this page
   :local:
   :depth: 2

GitBugRepo
----------

.. autoclass:: ezgb.GitBugRepo
   :members:
   :undoc-members:

Data classes
------------

Bug
^^^

.. autoclass:: ezgb.Bug
   :members:
   :undoc-members:

Comment
^^^^^^^

.. autoclass:: ezgb.Comment
   :members:
   :undoc-members:

Identity
^^^^^^^^

.. autoclass:: ezgb.Identity
   :members:
   :undoc-members:

Enums
-----

Status
^^^^^^

.. autoclass:: ezgb.Status
   :members:
   :undoc-members:

Exceptions
----------

All exceptions inherit from :class:`~ezgb.EzgbError`, so you can catch
that base class if you want to handle any ezgb error in one place.

.. autoclass:: ezgb.EzgbError
   :show-inheritance:

.. autoclass:: ezgb.BugNotFoundError
   :show-inheritance:

.. autoclass:: ezgb.AmbiguousBugIdError
   :show-inheritance:

.. autoclass:: ezgb.IdentityNotFoundError
   :show-inheritance:

.. autoclass:: ezgb.UnsupportedFormatError
   :show-inheritance:

.. autoclass:: ezgb.GitError
   :show-inheritance:

.. autoclass:: ezgb.CliError
   :show-inheritance:
