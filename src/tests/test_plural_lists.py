import collections
from copy import deepcopy

import jsonapi
import responses

from .constants import host
from .payloads import Payloads

_api = jsonapi.JsonApi(host=host, auth="test_api_key")


@_api.register
class Child(jsonapi.Resource):
    TYPE = "children"


@_api.register
class Parent(jsonapi.Resource):
    TYPE = "parents"


child_payloads = Payloads('children', singular_type="child")


PAYLOAD = {'data': {
    'type': "parents",
    'id': "1",
    'attributes': {'name': "parent 1"},
    'relationships': {'children': {
        'data': [{'type': "children", 'id': "1"},
                 {'type': "children", 'id': "2"}],
        'links': {'related': "/parents/1/children"},
    }},
}}


def make_simple_assertions(parent):
    assert isinstance(parent.children, collections.abc.Sequence)
    assert parent.children[0].id == "1"
    assert parent.children[1].id == "2"


def test_plural_list():
    parent = Parent(PAYLOAD)
    make_simple_assertions(parent)
    parent = Parent(PAYLOAD['data'])
    make_simple_assertions(parent)
    parent = Parent(**PAYLOAD['data'])
    make_simple_assertions(parent)


def test_included():
    payload = deepcopy(PAYLOAD)
    payload['included'] = child_payloads[1:3]
    parent = Parent(payload)
    make_simple_assertions(parent)

    assert parent.children[0].name == "child 1"
    assert parent.children[1].name == "child 2"


@responses.activate
def test_refetch():
    responses.add(responses.GET, f"{host}/parents/1/children",
                  json={'data': child_payloads[1:4]})

    parent = Parent(PAYLOAD)
    parent.fetch('children')  # No force=True
    assert len(parent.children) == 2

    parent.fetch('children', force=True)
    assert len(parent.children) == 3
    assert ([child.name for child in parent.children] ==
            [f"child {i}" for i in range(1, 4)])


@responses.activate
def test_save_with_included():
    payload = deepcopy(PAYLOAD)
    payload['included'] = child_payloads[1:3]
    responses.add(responses.PATCH, f"{host}/parents/1", json=payload)
    parent = Parent(PAYLOAD)
    parent.save(name="parent 1")
    assert [child.name for child in parent.children] == ["child 1", "child 2"]
