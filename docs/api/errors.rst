.. _error-responses:

###############
Error responses
###############

Protocol description
====================

Every response is JSON.

If the HTTP status is not OK (<200 or >=400), the response contains a JSON mapping, with the following attributes:

- ``code``: matches the HTTP status code (e.g ``400``)
- ``errno``: stable application-level error number (e.g. ``109``)
- ``error``: string description of error type (e.g. ``"Bad request"``)
- ``message``: context information (e.g. ``"Invalid request parameters"``)
- ``info``: online resource (e.g. URL to error details)
- ``details``: additional details (e.g. list of validation errors)

**Example response**

::

    {
        "code": 412,
        "errno": 114,
        "error": "Precondition Failed",
        "message": "Resource was modified meanwhile",
        "info": "https://server/docs/api.html#errors",
    }


Error codes
===========

Errors are meant to be managed at the project level.

However, a basic ref:`<set of errors codes <errors>`_ is provided in *Cliquet*.


Conflict errors
===============

When a record violates unicity constraints, a ``409 Conflict`` error response
is returned.

Additional information about conflicting record and field name will be
provided in the ``details`` field.

::

    {
        "code": 409,
        "errno": 122,
        "error": "Conflict",
        "message": "Conflict of field url on record eyjafjallajokull"
        "info": "https://server/docs/api.html#errors",
        "details": {
            "field": "url",
            "record": {
                "id": "eyjafjallajokull",
                "last_modified": 1430140411480,
                "url": "http://mozilla.org"
            }
        }
    }


Validation errors
=================

When multiple validation errors occur on a request, the first one is presented
in the message.

The full list of validation errors is provided in the ``details`` field.

::

    {
        "code": 400,
        "errno": 109,
        "error": "Bad Request",
        "message": "Invalid posted data",
        "info": "https://server/docs/api.html#errors",
        "details": [
            {
                "description": "42 is not a string: {'name': ''}",
                "location": "body",
                "name": "name"
            }
        ]
    }
