import re

import colander
from cornice import resource
from cornice.schemas import CorniceSchema
from pyramid.httpexceptions import (HTTPNotModified, HTTPPreconditionFailed,
                                    HTTPMethodNotAllowed,
                                    HTTPNotFound, HTTPConflict)
import six
from six.moves.urllib.parse import urlencode

from cliquet.storage import exceptions as storage_exceptions, Filter, Sort
from cliquet import errors
from cliquet import schema
from cliquet.utils import (
    COMPARISON, classname, native_value, decode_token, encode_token
)


def crud(**kwargs):
    """
    Decorator for resource classes.

    This allows to bring default parameters for Cornice ``resource()``.
    """
    def wrapper(klass):
        resource_name = klass.__name__.lower()
        params = dict(collection_path='/{0}s'.format(resource_name),
                      path='/{0}s/{{id}}'.format(resource_name),
                      description='Collection of {0}'.format(resource_name),
                      error_handler=errors.json_error,
                      cors_origins=('*',),
                      depth=2)
        params.update(**kwargs)

        return resource.resource(**params)(klass)
    return wrapper


class ResourceSchema(colander.MappingSchema):
    """Base resource schema.

    It brings common fields and behaviour for all inherited schemas:

    * ``id``
    * ``last_modified``
    """
    id = colander.SchemaNode(colander.String(), missing=colander.drop)
    last_modified = schema.TimeStamp()

    class Options:
        """
        Resource schema options.

        It let you configure the:
        * ``readonly_fields``: Fields that cannot be updated
        * ``unique_fields``: Fields that must have unique values for the user
          collection.

        """
        readonly_fields = ('id', 'last_modified')
        unique_fields = ('id', 'last_modified')

    def is_readonly(self, field):
        """Return True if specified field name is read-only.

        :param field: the field name in the schema
        :type field: string
        :returns: `True` if the specified field is read-only,
            `False` otherwise.
        :rtype: boolean
        """
        return field in self.Options.readonly_fields


class BaseResource(object):
    """Base resource class providing every endpoint."""
    mapping = ResourceSchema()
    """Schema to validate records"""

    validate_schema_for = ('POST', 'PUT')
    """HTTP verbs for which the schema must be validated"""

    id_field = 'id'
    """Name of *id* field in resource schema"""

    modified_field = 'last_modified'
    """Name of *last modified* field in resource schema"""

    deleted_mark = ('deleted', True)
    """Field and value of deleted status in records"""

    def __init__(self, request):
        self.request = request
        self.db = request.db
        self.db_kwargs = dict(resource=self,
                              user_id=request.authenticated_userid)
        self.known_fields = [c.name for c in self.mapping.children]
        self.timestamp = self.db.collection_timestamp(**self.db_kwargs)

    @property
    def name(self):
        """Resource name, unique."""
        return classname(self)

    @property
    def schema(self):
        """Resource schema, depending on HTTP verb."""
        colander_schema = self.mapping

        if self.request.method not in self.validate_schema_for:
            # No-op since payload is not validated against schema
            colander_schema = colander.MappingSchema(unknown='preserve')

        return CorniceSchema.from_colander(colander_schema)

    def raise_invalid(self, location='body', **kwargs):
        """Helper to raise a validation error.

        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest`
        """
        self.request.errors.add(location, **kwargs)
        response = errors.json_error(self.request.errors)
        raise response

    def fetch_record(self):
        """Fetch current view related record, and raise 404 if missing.

        :raises: :class:`pyramid.httpexceptions.HTTPNotFound`
        :returns: the record from storage
        :rtype: dict
        """
        try:
            record_id = self.request.matchdict['id']
            return self.db.get(record_id=record_id,
                               **self.db_kwargs)
        except storage_exceptions.RecordNotFoundError:
            response = HTTPNotFound(
                body=errors.format_error(
                    code=HTTPNotFound.code,
                    errno=errors.ERRORS.INVALID_RESOURCE_ID,
                    error=HTTPNotFound.title),
                content_type='application/json')
            raise response

    def process_record(self, new, old=None):
        """Hook to post-process records and introduce specific logics
        or validation.
        """
        new = self.preprocess_record(new, old)
        return new

    def preprocess_record(self, new, old=None):
        return new

    def merge_fields(self, record, changes):
        """Merge changes into current record fields.
        """
        for field, value in changes.items():
            has_changed = record.get(field, value) != value
            if self.mapping.is_readonly(field) and has_changed:
                error = 'Cannot modify {0}'.format(field)
                self.raise_invalid(name=field, description=error)

        updated = record.copy()
        updated.update(**changes)
        return self.validate(updated)

    def validate(self, record):
        """Validate specified record against resource schema.
        Raise 400 if not valid."""
        try:
            return self.mapping.deserialize(record)
        except colander.Invalid as e:
            # Transform the errors we got from colander into cornice errors
            for field, error in e.asdict().items():
                self.request.errors.add('body', name=field, description=error)
            raise errors.json_error(self.request.errors)

    def add_timestamp_header(self, response):
        """Add current timestamp in response headers, when request comes in.
        """
        timestamp = six.text_type(self.timestamp).encode('utf-8')
        response.headers['Last-Modified'] = timestamp

    def raise_304_if_not_modified(self, record=None):
        """Raise 304 if current timestamp is inferior to the one specified
        in headers.

        :raises: :class:`pyramid.httpexceptions.HTTPNotModified`
        """
        modified_since = self.request.headers.get('If-Modified-Since')

        if modified_since:
            modified_since = int(modified_since)

            if record:
                current_timestamp = record[self.modified_field]
            else:
                current_timestamp = self.db.collection_timestamp(
                    **self.db_kwargs)

            if current_timestamp <= modified_since:
                response = HTTPNotModified()
                self.add_timestamp_header(response)
                raise response

    def raise_412_if_modified(self, record=None):
        """Raise 412 if current timestamp is superior to the one
        specified in headers.

        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        """
        unmodified_since = self.request.headers.get('If-Unmodified-Since')

        if unmodified_since:
            unmodified_since = int(unmodified_since)

            if record:
                current_timestamp = record[self.modified_field]
            else:
                current_timestamp = self.db.collection_timestamp(
                    **self.db_kwargs)

            if current_timestamp > unmodified_since:
                error_msg = 'Resource was modified meanwhile'
                response = HTTPPreconditionFailed(
                    body=errors.format_error(
                        code=HTTPPreconditionFailed.code,
                        errno=errors.ERRORS.MODIFIED_MEANWHILE,
                        error=HTTPPreconditionFailed.title,
                        message=error_msg),
                    content_type='application/json')
                self.add_timestamp_header(response)
                raise response

    def raise_conflict(self, exception):
        """Helper to raise conflict responses.

        :param exception: the original unicity error
        :type exception: :class:`cliquet.storage.exceptions.UnicityError`
        :raises: :class:`pyramid.httpexceptions.HTTPConflict`
        """
        field = exception.field
        existing = exception.record[self.id_field]
        message = 'Conflict of field {0} on record {1}'.format(field, existing)
        response = HTTPConflict(
            body=errors.format_error(
                code=HTTPConflict.code,
                errno=errors.ERRORS.CONSTRAINT_VIOLATED,
                error=HTTPConflict.title,
                message=message),
            content_type='application/json')
        response.existing = exception.record
        raise response

    def _extract_filters(self, queryparams=None):
        """Extracts filters from QueryString parameters."""
        if not queryparams:
            queryparams = self.request.GET

        filters = []

        for param, value in queryparams.items():
            param = param.strip()
            value = native_value(value)

            # Ignore specific fields
            if param.startswith('_') and param not in ('_since', '_to'):
                continue

            # Handle the _since specific filter.
            if param in ('_since', '_to'):
                if not isinstance(value, six.integer_types):
                    error_details = {
                        'name': param,
                        'location': 'querystring',
                        'description': 'Invalid value for _since'
                    }
                    self.raise_invalid(**error_details)

                if param == '_since':
                    operator = COMPARISON.GT
                else:
                    operator = COMPARISON.LT
                filters.append(
                    Filter(self.modified_field, value, operator)
                )
                continue

            m = re.match(r'^(min|max|not|lt|gt)_(\w+)$', param)
            if m:
                keyword, field = m.groups()
                operator = getattr(COMPARISON, keyword.upper())
            else:
                operator, field = COMPARISON.EQ, param

            if field not in self.known_fields:
                error_details = {
                    'name': None,
                    'location': 'querystring',
                    'description': "Unknown filter field '{0}'".format(param)
                }
                self.raise_invalid(**error_details)

            filters.append(Filter(field, value, operator))

        return filters

    def _extract_sorting(self):
        """Extracts filters from QueryString parameters."""
        specified = self.request.GET.get('_sort', '').split(',')
        sorting = []
        modified_field_used = self.modified_field in specified
        for field in specified:
            field = field.strip()
            m = re.match(r'^([\-+]?)(\w+)$', field)
            if m:
                order, field = m.groups()

                if field not in self.known_fields:
                    error_details = {
                        'name': None,
                        'location': 'querystring',
                        'description': "Unknown sort field '{0}'".format(field)
                    }
                    self.raise_invalid(**error_details)

                direction = -1 if order == '-' else 1
                sorting.append(Sort(field, direction))

        if not modified_field_used:
            # Add a sort by the ``modified_field`` in descending order
            # useful for pagination
            sorting.append(Sort(self.modified_field, -1))
        return sorting

    def _build_pagination_rules(self, sorting, last_record, rules=None):
        """Return the list of rules for a given sorting attribute and
        last_record.

        """
        if rules is None:
            rules = []

        rule = []
        next_sorting = sorting[:-1]

        for field, _ in next_sorting:
            rule.append(Filter(field, last_record.get(field), COMPARISON.EQ))

        field, direction = sorting[-1]

        if direction == -1:
            rule.append(Filter(field, last_record.get(field), COMPARISON.LT))
        else:
            rule.append(Filter(field, last_record.get(field), COMPARISON.GT))

        rules.append(rule)

        if len(next_sorting) == 0:
            return rules

        return self._build_pagination_rules(next_sorting, last_record, rules)

    def _extract_pagination_rules_from_token(self, sorting):
        """Get pagination params."""
        queryparams = self.request.GET
        settings = self.request.registry.settings
        paginate_by = settings.get('cliquet.paginate_by')
        limit = queryparams.get('_limit', paginate_by)
        if limit:
            try:
                limit = int(limit)
            except ValueError:
                error_details = {
                    'name': None,
                    'location': 'querystring',
                    'description': "_limit should be an integer"
                }
                self.raise_invalid(**error_details)

        token = queryparams.get('_token', None)
        filters = []
        if token:
            try:
                last_record = decode_token(token)
            except (ValueError, TypeError):
                error_details = {
                    'name': None,
                    'location': 'querystring',
                    'description': "_token should be valid base64 JSON encoded"
                }
                self.raise_invalid(**error_details)

            filters = self._build_pagination_rules(sorting, last_record)
        return filters, limit

    def _next_page_url(self, sorting, limit, last_record):
        """Build the Next-Page header from where we stopped."""
        queryparams = self.request.GET.copy()
        queryparams['_limit'] = limit
        queryparams['_token'] = self._build_pagination_token(
            sorting, last_record)
        return '%s%s?%s' % (self.request.host_url, self.request.path_info,
                            urlencode(queryparams))

    def _build_pagination_token(self, sorting, last_record):
        """Build a pagination token.

        It is a base64 JSON object with the sorting fields values of
        the last_record.

        """
        token = {}

        for field, _ in sorting:
            token[field] = last_record[field]

        return encode_token(token)

    #
    # End-points
    #

    @resource.view(
        permission='readonly',
        cors_headers=('Next-Page', 'Total-Records', 'Last-Modified')
    )
    def collection_get(self):
        """Collection `GET` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPNotModified`
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest` if filters or
            sorting are invalid.
        """
        self.add_timestamp_header(self.request.response)
        self.raise_304_if_not_modified()
        self.raise_412_if_modified()

        filters = self._extract_filters()
        sorting = self._extract_sorting()
        pagination_rules, limit = self._extract_pagination_rules_from_token(
            sorting)

        include_deleted = self.modified_field in [f[0] for f in filters]

        records, total_records = self.db.get_all(
            filters=filters,
            sorting=sorting,
            pagination_rules=pagination_rules,
            limit=limit,
            include_deleted=include_deleted,
            **self.db_kwargs)

        headers = self.request.response.headers
        headers['Total-Records'] = ('%s' % total_records).encode('utf-8')

        if limit and len(records) == limit and total_records > limit:
            next_page = self._next_page_url(sorting, limit, records[-1])
            headers['Next-Page'] = next_page.encode('utf-8')

        body = {
            'items': records,
        }

        return body

    @resource.view(permission='readwrite')
    def collection_post(self):
        """Collection `POST` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest`
        """
        self.raise_412_if_modified()

        new_record = self.process_record(self.request.validated)
        try:
            record = self.db.create(record=new_record, **self.db_kwargs)
        except storage_exceptions.UnicityError as e:
            self.raise_conflict(e)

        self.request.response.status_code = 201
        return record

    @resource.view(permission='readwrite')
    def collection_delete(self):
        """Collection `DELETE` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPMethodNotAllowed` if not
            enabled in configuration.
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest` if filters or
            sorting are invalid.
        """
        settings = self.request.registry.settings
        enabled = settings.get('cliquet.delete_collection_enabled', 'true')
        if not native_value(enabled):
            raise HTTPMethodNotAllowed()

        self.raise_412_if_modified()

        filters = self._extract_filters()
        deleted = self.db.delete_all(filters=filters, **self.db_kwargs)

        body = {
            'items': deleted,
        }

        return body

    @resource.view(permission='readonly', cors_headers=('Last-Modified',))
    def get(self):
        """Record `GET` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPNotFound`
        :raises: :class:`pyramid.httpexceptions.HTTPNotModified`
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        """
        self.add_timestamp_header(self.request.response)
        record = self.fetch_record()
        self.raise_304_if_not_modified(record)
        self.raise_412_if_modified(record)

        return record

    @resource.view(permission='readwrite')
    def put(self):
        """Record `PUT` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPConflict`
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest`
        """
        record_id = self.request.matchdict['id']

        try:
            existing = self.db.get(record_id=record_id,
                                   **self.db_kwargs)
            self.raise_412_if_modified(existing)
        except storage_exceptions.RecordNotFoundError:
            existing = None

        new_record = self.request.validated

        new_id = new_record.setdefault(self.id_field, record_id)
        if new_id != record_id:
            error_msg = 'Record id does not match existing record'
            self.raise_invalid(name=self.id_field, description=error_msg)

        new_record = self.process_record(new_record, old=existing)

        try:
            record = self.db.update(record_id=record_id,
                                    record=new_record,
                                    **self.db_kwargs)
        except storage_exceptions.UnicityError as e:
            self.raise_conflict(e)

        return record

    @resource.view(permission='readwrite')
    def patch(self):
        """Record `PATCH` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPNotFound`
        :raises: :class:`pyramid.httpexceptions.HTTPConflict`
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        :raises: :class:`pyramid.httpexceptions.HTTPBadRequest`
        """
        record = self.fetch_record()
        self.raise_412_if_modified(record)

        changes = self.request.json

        updated = self.merge_fields(record, changes=changes)

        updated = self.process_record(updated, old=record)

        nothing_changed = not any([record.get(k) != updated.get(k)
                                   for k in changes.keys()])
        if nothing_changed:
            return record

        try:
            record = self.db.update(record_id=record[self.id_field],
                                    record=updated,
                                    **self.db_kwargs)
        except storage_exceptions.UnicityError as e:
            self.raise_conflict(e)

        return record

    @resource.view(permission='readwrite')
    def delete(self):
        """Record `DELETE` endpoint.

        :raises: :class:`pyramid.httpexceptions.HTTPNotFound`
        :raises: :class:`pyramid.httpexceptions.HTTPPreconditionFailed`
        """
        record = self.fetch_record()
        self.raise_412_if_modified(record)

        deleted = self.db.delete(record_id=record[self.id_field],
                                 **self.db_kwargs)
        return deleted
