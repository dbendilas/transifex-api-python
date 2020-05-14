""" Mini-SDK for building client libraries on top of a JSONAPI server
    implementation. Usage:

        >>> import jsonapi
        >>> jsonapi.setup('http://api.com', 'VERY_SECRET_API_TOKEN')

        >>> class Person(jsonapi.Resource):
        ...     TYPE = 'people'
        ...     EDITABLE = 'name'

        >>> people = Person.list(filters={'age[gt]': 28}, include=['parent'])
        >>> for person in people.all():
        ...     # `.a` is a shortcut to `.attributes`
        ...     print(person.a['name'])
        ...
        ...     # `.r` is a shortcut to `.related`
        ...     print(person.r['parent'].a['name'])
        ...
        ...     # Fetches 'home' singular relationship
        ...     person.fetch('home')
        ...     print(person.r['home'].a['address'])
        ...
        ...     # Fetches 'hobbies' plural relationship
        ...     person.fetch('hobbies')
        ...     print((hobby.a['name'] for hobby in person.r['hobbies'].all()))

        >>> person_a, person_b = people[:2]

        >>> person_a.a['name'] = "Billy"
        >>> person_a.save('name')
        >>> # You can omit 'name' since it's the EDITABLE field
        >>> person_a.save()

        >>> person_a.change('parent', person_b.R['parent']['data'])

    Assumptions about the server JSONAPI server implementation (apart from
    those listed in the specification):

    - Authorization is done via the Authorization header.

    - Collection and item endpoints always have the form `/<type>` and
      `/<type>/<id>` respectively, ie based on the 'TYPE' attribute of the
      Resource subclasses we are able to determine the URLs where the instances
      will be fetched from/posted to.

    - A relationship can be either a:

        1. Null singular relationship; null singular relationships are expected
           to have the `null` value, example:

            {id: XXX,
             type: XXX,
             attributes: {...},
             links: {...},
             relationships: {parent: null,
                             ...}}

        2. Not null singular relationship; not null singular relationships are
           expected to have a required 'data' field and an optional 'links'
           field, with 'data' being a resource identifier and 'links' having a
           'related' field. Example:

            {id: XXX,
             type: XXX,
             attributes: {...},
             links: {...},
             relationships: {parent: {links: {related: XXX, ...},
                                      data: {type: XXX, id: XXX}},
                             ...}}

        3. Plural relationship; plural relationships are expected to **not**
           have a 'data'field, the 'links' field to be required, with 'links'
           having a 'related' field. Example:

            {id: XXX,
             type: XXX,
             attributes: {...},
             links: {...},
             relationships: {children: {links: {related: XXX, ...}},
                             ...}}

    - Client-generated IDs are not supported, ie:

        - Calling `.save()` on a resource instance with a null ID will result
          in a HTTP POST request that will create the instance on the server
          and the ID will be overwritten by the response

        - Calling `.save()` on a resource instance with a non-null ID will
          result in a PATCH HTTP request that will update the instance on the
          server

"""

from copy import deepcopy

from .globals import _jsonapi_global
from .queryset import Queryset
from .requests import _jsonapi_request


class _JsonApiMeta(type):
    """ Fills up our global class registry whenever a subclass of Resource is
        defined.
    """

    def __new__(cls, name, bases, dct):
        klass = super().__new__(cls, name, bases, dct)
        if dct.get('TYPE') is not None:
            _jsonapi_global.registry[dct['TYPE']] = klass
        return klass


class Resource(metaclass=_JsonApiMeta):
    """ Subclass like this:

            >>> class Foo(jsonapi.Resource):
            ...     TYPE = "foos"
            ...     EDITABLE = ['name', 'age', 'parent']

        EDITABLE values can either be names of attributes or relationships.
    """

    TYPE = None
    EDITABLE = None

    # Creation
    def __init__(self, data=None, *, id=None, attributes=None,
                 relationships=None, links=None, related=None, type=None):
        """ Initialize an API resource instance when you know the type.

            You can either provide:

            - Specific 'id', 'attributes', 'relationships', 'links' and/or
              'related'

                >>> user = User(id="1")
                >>> user.reload()  # will use 'type' and 'id' to fetch from API

                >>> parent = ...
                >>> user = User(attributes={'username': "Bill"},
                ...             relationships={'parent': parent})
                >>> user.save()  # Will receive an 'id' from the API

            - The json body of a response

                >>> response = requests.get("http://api.com/users/1")
                >>> user = User(response.json())
                >>> user = User(**response.json()['data'])

            - Another API resource instance's relationship or relationship data

                >>> user = ...

                >>> # {'data': {'type': "parents", 'id': 1}}
                >>> parent = Parent(user.R['parent'])

                >>> # {'type': "parents", 'id': 1}
                >>> parent = Parent(user.R['parent']['data'])

                >>> parent.reload()  # To fetch the rest of the fields
        """

        if type is not None and type != self.TYPE:
            raise ValueError("Invalid type")

        if data is not None:
            # Maybe a HTTP response body was passed:
            # - Parent(requests.get('http://api.com/parents/1').json())
            # - Parent(requests.get('http://api.com/parents/1').json()['data'])
            # Or a relationship:
            # - `Parent(user.R['parent'])`
            # - `Parent({'data': {'type': "parents", 'id': "1"}})`
            if 'data' in data:
                data = data['data']
            self._overwrite(**data)
        else:
            self._overwrite(id=id, attributes=attributes,
                            relationships=relationships, links=links,
                            related=related)

    def _overwrite(self, id=None, attributes=None, relationships=None,
                   links=None, related=None, included=None, type=None):
        """ Write to the basic attributes of Resource. Used by '__init__',
            'reload', '__copy__' and 'save'
        """

        self.id = id

        if attributes is not None:
            self.attributes = deepcopy(attributes)
        else:
            self.attributes = {}

        self.relationships = {}
        if relationships is not None:
            for key, value in deepcopy(relationships).items():
                if isinstance(value, Resource):
                    self.R[key] = value.as_relationship()
                else:
                    self.R[key] = value

        if links is not None:
            self.links = deepcopy(links)
        else:
            self.links = {}

        self.related = {}
        for relationship_name, relationship in self.R.items():
            if relationship is None:
                # Singular null
                self.r[relationship_name] = None
            elif 'data' in relationship:
                # Singular not null
                self.r[relationship_name] = Resource.new(relationship)

        if included is not None:
            included = {(item['type'], item['id']): item for item in included}
            for relationship_name, relationship in self.R.items():
                if relationship is None or 'data' not in relationship:
                    continue
                key = (relationship['data']['type'],
                       relationship['data']['id'])
                if key in included:
                    self.r[relationship_name] = Resource.new(included[key])

        if related is not None:
            self.r.update(deepcopy(related))

    @classmethod
    def new(cls, data=None, *, type=None, **kwargs):
        """ Initialize an API resource type when you don't know the type,
            provided that a subclass with that type was defined.

            Requires a 'type' argument or a 'data' with a 'type' field to
            function properly.

                >>> class Parent(jsonapi.Resource):
                ...     TYPE = "parents"

                >>> parent = jsonapi.Resource.new(type="parents", id="1")
                >>> # 'parent' is now of type 'Parent'
                >>> parent.reload()  # To fetch the rest of the fields

                >>> response = requests.get('http://api.com/parents/1')
                >>> parent = jsonapi.Resource.new(response.json())
                >>> parent = jsonapi.Resource.new(response.json()['data'])
                >>> parent = jsonapi.Resource.new(**response.json()['data'])
        """

        if data is not None:
            if 'data' in data:
                data = data['data']
            return cls.new(**data)
        else:
            klass = _jsonapi_global.registry.get(type, Resource)
            return klass(**kwargs)

    @classmethod
    def as_resource(cls, data):
        """ Little convenience function when we don't know if we are dealing
            with a Resource instance or a dict describing a relationship.
        """

        try:
            return cls.new(data)
        except Exception:
            return data

    # Shortcuts
    @property
    def a(self):
        """ Shortcut for `attributes` """

        # Doing this instead of `self.a = self.attributes` in `__init__` in
        # order to not pollute `self.__dict__`
        return self.attributes

    @property
    def R(self):
        """ Shortcut for `relationships` """

        # Doing this instead of `self.R = self.relationships` in `__init__` in
        # order to not pollute `self.__dict__`
        return self.relationships

    @property
    def r(self):
        """ Shortcut for `related` """

        # Doing this instead of `self.r = self.related` in `__init__` in order
        # to not pollute `self.__dict__`
        return self.related

    def __getattr__(self, attr):
        if attr in ('a', 'attributes', 'R', 'relationships', 'r', 'related',
                    'id', 'links'):
            return super().__getattribute__(attr)
        elif attr in self.a:
            return self.a[attr]
        elif attr in self.r:
            return self.r[attr]
        else:
            return super().__getattribute__(attr)

    def __setattr__(self, attr, value):
        if attr in ('id', 'attributes', 'relationships', 'related', 'links'):
            super().__setattr__(attr, value)
        elif attr in self.a:
            self.a[attr] = value
        elif attr in self.R:
            if self.R[attr] is None or 'data' in self.R[attr]:
                value = Resource.as_resource(value)
                self.R[attr] = value.as_relationship()
                self.r[attr] = value
            else:
                raise AttributeError(
                    f"You can't set the '{attr}' relationship on a "
                    f"{self.__class__.__name__} instance because it is a "
                    f"plural relationship. Use '.add()', '.remove()' or "
                    f"'.reset()' instead."
                )
        else:
            super().__setattr__(attr, value)

    # Fetching
    def reload(self, *, include=None):
        """ Fetch fresh data from the server for the object.  """

        url = self._get_url()
        params = None
        if include is not None:
            params = {'include': ','.join(include)}
        response_body = _jsonapi_request('get', url, params=params)
        self._overwrite(included=response_body.get('included'),
                        **response_body['data'])

    @classmethod
    def get(cls, id, *, type=None, include=None):
        """ Usage:

                >>> foo = Foo.get('foo_id')
                >>> # or
                >>> Foo.get('foo_id', include=['parent'])

            Works with 'type' attribute in case the class name is unknown (but
            registered):

                >>> foo = jsonapi.Resource.get(1, type="users")
        """

        if type is not None and cls.TYPE is None:
            instance = cls.new(id=id, type=type)
        else:
            instance = cls(id=id)
        instance.reload(include=include)
        return instance

    @classmethod
    def list(cls):
        return Queryset(f"/{cls.TYPE}")

    def _queryset_method(method):
        def _method(cls, *args, **kwargs):
            return getattr(Queryset(f"/{cls.TYPE}"), method)(*args, **kwargs)
        return classmethod(_method)

    filter = _queryset_method('filter')
    page = _queryset_method('page')
    include = _queryset_method('include')
    sort = _queryset_method('sort')
    fields = _queryset_method('fields')
    extra = _queryset_method('extra')

    def fetch(self, *relationship_names, force=False):
        """ Fetches 'relationship', if it wasn't included when fetching 'self';
            `force=True` supported. Usage:

                >>> foo.fetch('parent')

            Related object will be available after that:

                >>> print(foo.r['parent'].a['name'])

            Supports plural relationships, but only one page will be available:

                >>> foo.fetch('children')
                >>> # Only first page
                >>> print([child.a['name'] for child in foo.r['children']])
                >>> # All pages
                >>> print([child.a['name']
                ...        for child in foo.r['children'].all()])
        """

        for relationship_name in relationship_names:
            if relationship_name not in self.R:
                raise ValueError(f"{repr(self)} doesn't have relationship "
                                 f"'{relationship_name}'")

            relationship = self.R[relationship_name]

            if relationship is None:
                continue

            is_singular_fetched = (
                isinstance(self.r.get(relationship_name), Resource) and
                (self.r[relationship_name].a or self.r[relationship_name].R)
            )
            is_plural_fetched = isinstance(self.r.get(relationship_name),
                                           Queryset)
            if (is_singular_fetched or is_plural_fetched) and not force:
                # Has been fetched already
                continue

            if 'data' in relationship:
                # Singular relationship
                self.r[relationship_name].reload()
            else:
                # Plural relationship
                url = relationship['links']['related']
                self.r[relationship_name] = Queryset(url)

    # Editing
    def save(self, *fields):
        """ For new instances (that have `.id == None`), everything will be
            saved and 'id' and other server-generated fields will be set.

            For existing instances, if `fields` or `cls.EDITABLE` is set, then
            only these fields will be saved.

            Usage:
                >>> class Foo(Resource):
                ...     type = 'foos'
                ...     EDITABLE = ['name']

                >>> foo = Foo.get(1)
                >>> foo.a['name'] = 'footastic'
                >>> foo = foo.save()
                >>> # or
                >>> foo = foo.save('name', ...)
        """

        if self.id:
            response_body = self._save_existing(*fields)
        else:
            response_body = self._save_new()
        data = response_body['data']

        related = deepcopy(self.r)
        for relationship_name, related_instance in list(related.items()):
            if isinstance(related_instance, Queryset):
                continue  # Plural relationship

            try:
                current_id = related_instance.id
            except Exception:
                current_id = None
            try:
                new_id = data['relationships'][relationship_name]['data']['id']
            except Exception:
                new_id = None
            if current_id != new_id:
                if new_id is not None:
                    related[relationship_name] = Resource.new(
                        data['relationships'][relationship_name]
                    )
                else:
                    del related[relationship_name]

        self._overwrite(related=related, **data)

    def _save_existing(self, *fields):
        payload = self.as_resource_identifier()
        editable_fields = fields or self.EDITABLE
        if editable_fields is not None:
            for field in editable_fields:
                if field in self.a:
                    payload.setdefault('attributes', {})[field] = self.a[field]
                elif field in self.R:
                    payload.setdefault('relationships', {})[field] =\
                        self.R[field]
        else:
            if self.a:
                payload['attributes'] = self.a
            if self.R:
                payload['relationships'] = self.R
        return _jsonapi_request('patch', self._get_url(),
                                json={'data': payload})

    def _save_new(self):
        url = f"/{self.TYPE}"
        payload = {'type': self.TYPE}
        if self.a:
            payload['attributes'] = self.a
        if self.R:
            payload['relationships'] = self.R
        return _jsonapi_request('post', url, json={'data': payload})

    @classmethod
    def create(cls, *args, **kwargs):
        """ Usage:

            >>> foo = Foo.create(attributes={...}, relationships={...})
        """

        if cls.TYPE is not None:
            instance = cls(*args, **kwargs)
        else:
            instance = cls.new(*args, **kwargs)
        if instance.id is not None:
            raise ValueError("'id' supplied as part of a new instance")
        instance.save()
        return instance

    def delete(self):
        """ Deletes a resource from the API. Usage:

                >>> foo.delete()
        """

        _jsonapi_request('delete', self._get_url())
        self.id = None

    # Editing relationshps
    def change(self, field, value):
        """ Change a singular relationship. Usage:

                >>> # Change `child`'s parent from `parent_a` to `parent_b`
                >>> parent_a, parent_b = Parent.list()[:2]
                >>> child = Child.get(XXX)
                >>> assert child.R['parent'] == parent_a
                >>> child.change('parent', parent_b)

            Also works with resource identifiers in case we don't have the full
            Resource instance:

                >>> # Make sure `child_a` and `child_b` have the same parent,
                >>> # without fetching the parent
                >>> child_a, child_b = Child.list()[:2]
                >>> child_b.change('parent', child_a.R['parent']['data'])

            Note: Depending on the API implementation, this can probably be
            also achieved by changing the relationship and saving:

                >>> # Change `child`'s parent from `parent_a` to `parent_b`
                >>> parent_a, parent_b = Parent.list()[:2]
                >>> child = Child.get(XXX)
                >>> assert child.R['parent']['data']['id'] == parent_a.id
                >>> child.R['parent'] = {
                ...     'data': parent_b.as_resource_identifier(),
                ... }
                >>> child.save('parent')
        """

        value = Resource.as_resource(value)
        self._edit_relationship('patch', field, value.as_resource_identifier())
        self.R[field]['data'] = value.as_resource_identifier()
        if self.r[field] != value:
            self.r[field] = value

    def add(self, field, values):
        """ Adds items to a plural relationship. Usage:

                >>> # Lets add 3 new children to `parent`
                >>> parent = Parent.get(XXX)
                >>> child_a, child_b, child_c = Child.list(
                ...     filters={'parent[ne]': parent.id},
                ... )[:3]
                >>> parent.add('children', [child_a, child_b, child_c])

            Also works with resource identifiers in case we don't have the
            Resource instances:

                >>> # Make sure parents of `child_a` and `child_b` become
                ...  # children of `grandparent`
                >>> grandparent.add('children', [child_a.R['parent']['data'],
                ...                              child_b.R['parent']['data']])

            If the plural relationship was previously fetched, it must be
            refetched for the changes to appear.

                >>> parent.add('children', ...)
                >>> parent.fetch('children', force=True)
        """

        self._edit_plural_relationship('post', field, values)

    def remove(self, field, values):
        """ Removes items from a plural relationship. Usage:

                >>> parent = Parent.get(XXX)
                >>> child_a, child_b = Child.list(
                ...     filters={'parent': parent.id},
                ... )[:2]
                >>> parent.remove('children', [child_a, child_b])

            Also works with resource identifiers in case we don't have the
            Resource instances:

                >>> # Make sure parents of `child_a` and `child_b` are no
                ... # longer children of `grandparent`
                >>> grandparent.remove('children',
                ...                    [child_a.R['parent']['data'],
                ...                     child_b.R['parent']['data']])

            If the plural relationship was previously fetched, it must be
            refetched for the changes to appear.

                >>> parent.remove('children', ...)
                >>> parent.fetch('children', force=True)
        """

        self._edit_plural_relationship('delete', field, values)

    def reset(self, field, values):
        """ Completely rewrites a plural relationship. Usage:

                >>> parent = Parent.get(XXX)
                >>> child_a, child_b, child_c = Child.list()[:3]
                >>> assert child_a.R['parent']['data']['id'] == parent.id
                >>> assert child_b.R['parent']['data']['id'] != parent.id
                >>> assert child_c.R['parent']['data']['id'] != parent.id

                >>> parent.reset('children', [child_b, child_c])

            If the plural relationship was previously fetched, it must be
            refetched for the changes to appear.

                >>> parent.reset('children', ...)
                >>> parent.fetch('children', force=True)
        """

        self._edit_plural_relationship('patch', field, values)

    def _edit_relationship(self, method, field, value):
        url = self.R[field]['links']['self']
        _jsonapi_request(method, url, json={'data': value})

    def _edit_plural_relationship(self, method, field, values):
        payload = []
        for item in values:
            payload.append(Resource.as_resource(item).as_resource_identifier())
        self._edit_relationship(method, field, payload)

    # Bulk actions
    @classmethod
    def bulk_delete(cls, items):
        """ Delete API resource instances in bulk. The server needs to support
            this using the 'bulk' profile with the
            'application/vnd.api+json;profile="bulk"' Content-Type header.

            Doesn't return anything, but will raise an exception if something
            went wrong.

            Usage:

                >>> foos = Foo.list(...)
                >>> Foo.bulk_delete(foos)
        """

        _jsonapi_request(
            'delete',
            f"/{cls.TYPE}",
            json={'data': [Resource.as_resource(item).as_resource_identifier()
                           for item in items]},
            bulk=True,
        )

    @classmethod
    def bulk_create(cls, items):
        """ Create API resource instances in bulk. The server needs to support
            this using the 'bulk' profile with the
            'application/vnd.api+json;profile="bulk"' Content-Type header.

            Accepts a list of:
                - (Unsaved) API resource instances
                - Dictionaries with (optional) 'attributes' and 'relationships'
                  fields
                - 2-tuples of 'attributes', 'relationships'
                - 'attributes'

            Returns a list of the created instances.

            Usage:

                >>> # Only attributes >>> result =
                Foo.bulk_create([{'username': "username1"}, ...
                {'username': "username2"}, ...
                {'username': "username3"}]) >>> result[0].id <<< 1 >>>
                result[0].a['username'] <<< 'username1'

                >>> # attributes and relationships >>> parent = ...  >>> result
                = Child.bulk_create( ...     [({'username': "username1"},
                {'parent': parent}), ...      ...] ... )
        """

        payload = []
        for item in items:
            attributes, relationships, id = cls._extract_from_item(item)
            if id is not None:
                raise ValueError("'id' supplied as part of a new instance")

            payload.append({'type': cls.TYPE})
            if attributes is not None:
                payload[-1]['attributes'] = attributes
            if relationships is not None:
                payload[-1]['relationships'] = {
                    key: Resource.as_resource(value).as_relationship()
                    for key, value in relationships.items()
                }

        response_body = _jsonapi_request('post', f"/{cls.TYPE}",
                                         json={'data': payload}, bulk=True)
        return Queryset.from_data(response_body)

    @classmethod
    def bulk_update(cls, items, fields=None):
        """ Update API resource instances in bulk. The server needs to support
            this using the 'bulk' profile with the
            'application/vnd.api+json;profile="bulk"' Content-Type header.

            Accepts a list of:
                - API resource instances (with IDs)
                - Dictionaries with (optional) 'attributes', 'relationships'
                  and (required) 'id' fields
                - 3-tuples of 'attributes', 'relationships', 'id'

            Returns a list of the updated instances.

            Usage:

                >>> foos = Foo.list(...)
                >>> for foo in foos:
                ...     foo.a['approved'] = True
                >>> foos = Foo.bulk_update(foos, ['approved'])
        """

        if fields is None:
            fields = cls.EDITABLE

        payload = []
        for item in items:
            attributes, relationships, id = cls._extract_from_item(item)
            if id is None:
                raise ValueError("'id' not supplied as part of an update "
                                 "operation")

            if fields:
                if attributes is not None:
                    attributes = {key: value
                                  for key, value in attributes.items()
                                  if key in fields}
                if relationships is not None:
                    relationships = {key: value
                                     for key, value in relationships.items()
                                     if key in fields}

            payload.append({'type': cls.TYPE, 'id': id})
            if attributes:
                payload[-1]['attributes'] = attributes
            if relationships:
                payload[-1]['relationships'] = {
                    key: Resource.as_resource(value).as_relationship()
                    for key, value in relationships.items()
                }

        response_body = _jsonapi_request('patch', f"/{cls.TYPE}",
                                         json={'data': payload}, bulk=True)
        return Queryset.from_data(response_body)

    @staticmethod
    def _extract_from_item(item):
        if isinstance(item, Resource):
            return item.a, item.R, item.id

        try:
            attributes, relationships, id = (
                item.get('attributes', None),
                item.get('relationships', None),
                item.get('id', None),
            )
        except AttributeError:
            try:
                attributes, relationships, id = item
            except ValueError:
                try:
                    attributes, relationships = item
                    id = None
                except ValueError:
                    attributes = item
                    relationships, id = None
        return attributes, relationships, id

    # Utils
    def __eq__(self, other):
        other = Resource.as_resource(other)
        return self.as_resource_identifier() == other.as_resource_identifier()

    def __repr__(self):
        if self.__class__ is Resource:
            class_name = "Unknown Resource"
        else:
            class_name = self.__class__.__name__

        if self.id is not None:
            details = self.id
        else:
            details = "Unsaved"

        return f"<{class_name}: {details}>"

    def __copy__(self):
        # Will eventually call `_overwrite` so `deepcopy` will be used
        return self.__class__(id=self.id, attributes=self.a,
                              relationships=self.r, links=self.links,
                              related=self.r)

    def as_resource_identifier(self):
        return {'type': self.TYPE, 'id': self.id}

    def as_relationship(self):
        return {'data': self.as_resource_identifier()}

    def _get_url(self):
        if 'self' in self.links:
            return self.links['self']
        else:
            return f"/{self.TYPE}/{self.id}"
