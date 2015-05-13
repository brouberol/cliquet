Ecosystem
#########

This section gathers information about extending *Cliquet*, and upcoming
third-party packages.

.. note::

    If you build a package that you would like to see listed here, just
    get in touch with us!


Extending Cliquet
=================

Pluggable components
--------------------

:term:`Pluggable` components can be substituted from configuration files,
as long as the replacement follows the original component API.

.. code-block:: ini

    # myproject.ini
    cliquet.logging_renderer = cliquet_fluent.FluentRenderer

This is the simplest way to extend *Cliquet*, but will be limited to its
existing components (cache, storage, log renderer, ...).

In order to add extra features, including external packages is the way to go!


Include external packages
-------------------------

Appart from usual python «*import and use*», *Pyramid* can include external
packages, which can bring views, event listeners etc.

.. code-block:: python
    :emphasize-lines: 11

    import cliquet
    from pyramid.config import Configurator


    def main(global_config, **settings):
        config = Configurator(settings=settings)

        cliquet.initialize(config, '0.0.1')
        config.scan("myproject.views")

        config.include('cliquet_elasticsearch')

        return config.make_wsgi_app()


Alternatively, packages can also be included via configuration:

.. code-block:: ini

    # myproject.ini
    pyramid.includes = cliquet_elasticsearch
                       pyramid_debugtoolbar


There are `many available packages`_, and it is straightforward to build one.

.. _curated list: https://github.com/ITCase/awesome-pyramid


Include me
----------

In order to be included, a package must define an ``includeme(config)`` function.

For example, in :file:`cliquet_elasticsearch/init.py`:

.. code-block:: python

    def includeme(config):
        settings = config.get_settings()

        config.add_view(...)


Custom backend
==============

As a simple example, let's add add another kind of cache backend to *Cliquet*.

:file:`cliquet_riak/cache.py`:

.. code-block:: python

    from cliquet.cache import CacheBase
    from riak import RiakClient


    class Riak(CacheBase):
        def __init__(self, **kwargs):
            self._client = RiakClient(**kwargs)
            self._bucket = self._client.bucket('cache')

        def set(self, key, value, ttl=None):
            key = self._bucket.new(key, data=value)
            key.store()
            if ttl is not None:
                # ...

        def get(self, key):
            fetched = self._bucked.get(key)
            return fetched.data

        #
        # ...see cache documentation for a complete API description.
        #


    def load_from_config(config):
        settings = config.get_settings()
        uri = settings['cliquet.cache_url']
        uri = urlparse.urlparse(uri)

        return Riak(pb_port=uri.port or 8087)


Once its package installed and available in Python path, this new backend type
can be specified in application configuration:

.. code-block:: ini

    # myproject.ini
    cliquet.cache_backend = cliquet_riak.cache


Adding features
===============

Another use-case would be to add extra-features, like indexing for example.

* Initialize an indexer on startup;
* Add a ``/search/{collection}/`` end-point;
* Index records manipulated by resources.


Inclusion and startup in :file:`cliquet_indexing/__init__.py`:

.. code-block:: python

    DEFAULT_BACKEND = 'cliquet_indexing.elasticsearch'

    def includeme(config):
        settings = config.get_settings()
        backend = settings.get('cliquet.indexing_backend', DEFAULT_BACKEND)
        indexer = config.maybe_dotted(backend)

        # Store indexer instance in registry.
        config.registry.indexer = indexer.load_from_config(config)

        # Activate end-points.
        config.scan('cliquet_indexing.views')


End-point definitions in :file:`cliquet_indexing/views.py`:

.. code-block:: python

    from cornice import Service

    search = Service(name="search",
                     path='/search/{resource_name}/',
                     description="Search")

    @search.post()
    def get_search(request):
        resource_name = request.matchdict['resource_name']
        query = request.body

        # Access indexer from views using registry.
        indexer = request.registry.indexer
        results = indexer.search(resource_name, query)

        return results


Example indexer class in :file:`cliquet_indexing/elasticsearch.py`:

.. code-block:: python

    class Indexer(...):
        def __init__(self, hosts):
            self.client = elasticsearch.Elasticsearch(hosts)

        def search(self, resource_name, query, **kwargs):
            try:
                return self.client.search(index=resource_name,
                                          doc_type=resource_name,
                                          body=query,
                                          **kwargs)
            except ElasticsearchException as e:
                logger.error(e)
                raise

        def index_record(self, resource, record):
            record_id = record[resource.id_field]
            try:
                index = self.client.index(index=resource.name,
                                          doc_type=resource.name,
                                          id=record_id,
                                          body=record,
                                          refresh=True)
                return index
            except ElasticsearchException as e:
                logger.error(e)
                raise


Indexed resource in :file:`cliquet_indexing/resource.py`:

.. code-block:: python

    class IndexedResource(cliquet.resource.BaseResource):
        def create_record(self, record):
            r = super(IndexedResource, self).create_record(self, record)

            indexer = self.request.registry.indexer
            indexer.index_record(self, record)

            return r

.. note::

    In this example, ``IndexedResource`` is inherited, and must hence be
    used explicitly as a base resource class in applications.
    A nicer pattern would be to trigger *Pyramid* events in *Cliquet* and
    let packages like this one plug listeners. If you're interested,
    `we started to discuss it <https://github.com/mozilla-services/cliquet/issues/32>`_!


JavaScript client
=================

One of the main goal of *Cliquet* is to ease the development of REST
microservices, most likely to be used in a JavaScript environment.

A client could look like this:

.. code-block:: javascript

    var client = new cliquet.Client({
        server: 'https://api.server.com',
        store: localforage
    });

    var articles = client.resource('/articles');

    articles.create({title: "Hello world"})
      .then(function (result) {
        // success!
      });

    articles.get('id-1234')
      .then(function (record) {
        // Read from local if offline.
      });

    articles.filter({
        title: {'$eq': 'Hello'}
      })
      .then(function (results) {
        // List of records.
      });

    articles.sync()
      .then(function (result) {
        // Synchronize offline store with server.
      })
      .catch(function (err) {
        // Error happened.
        console.error(err);
      });