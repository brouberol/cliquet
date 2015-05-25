Resource
########

.. _resource:

*Cliquet* provides a basic component to build resource oriented APIs.
In most cases, the main customization consists in defining the schema of the
records for this resource.


Full example
============

.. code-block:: python

    import colander

    from cliquet import resource
    from cliquet import schema
    from cliquet import utils


    class BookmarkSchema(resource.Schema):
        url = schema.URL()
        title = colander.SchemaNode(colander.String())
        favorite = colander.SchemaNode(colander.Boolean(), missing=False)
        device = colander.SchemaNode(colander.String(), missing='')

        class Options:
            readonly_fields = ('device',)
            unique_fields = ('url',)


    @resource.crud()
    class Bookmark(resource.Resource):
        mapping = BookmarkSchema()

        def process_record(self, new, old=None):
            if new['device'] != old['device']:
                new['device'] = self.request.headers.get('User-Agent')

            return new

See the :github:`ReadingList <mozilla-services/readinglist>` and
:github:`Kinto <mozilla-services/kinto>` projects source code for real use cases.


.. _resource-schema:

Schema
======

Override the base schema to add extra fields using the `Colander API <http://docs.pylonsproject.org/projects/colander/>`_.

.. code-block:: python

    class Movie(Schema):
        director = colander.SchemaNode(colander.String())
        year = colander.SchemaNode(colander.Int(),
                                   validator=colander.Range(min=1850))
        genre = colander.SchemaNode(colander.String(),
                                    validator=colander.OneOf(['Sci-Fi', 'Comedy']))

.. automodule:: cliquet.schema
    :members:


.. _resource-class:

Resource class
==============

In order to customize the resource URLs or behaviour on record
processing or fetching from storage, the class


.. automodule:: cliquet.resource
    :members:


Custom record ids
=================

By default, records ids are `UUID4 <http://en.wikipedia.org/wiki/Universally_unique_identifier>_`.

A custom record id generator can be set globally in :ref:`configuration`,
or at the resource level:

.. code-block :: python

    from cliquet import resource
    from cliquet import utils
    from cliquet.storage import generators


    class MsecId(generators.Generator):
        def __call__(self):
            return '%s' % utils.msec_time()


    @resource.crud()
    class Mushroom(resource.Resource):
        id_generator = MsecId()


Generators objects
::::::::::::::::::

.. automodule:: cliquet.storage.generators
    :members:
