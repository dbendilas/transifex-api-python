import json
from copy import deepcopy

import responses
import jsonapi

from .constants import host
from .payloads import Payloads


class Child(jsonapi.Resource):
    TYPE = "children"


class Parent(jsonapi.Resource):
    TYPE = "parents"


jsonapi.setup("test_api_key", host)


child_payloads = Payloads(
    'children', 'child',
    extra={'relationships': {
        'parent': {'data': {'type': "parents", 'id': "1"},
                   'links': {'self': "/children/1/relationships/parent",
                             'related': "/parents/1"}},
    }}
)
parent_payloads = Payloads(
    'parents',
    extra={'relationships': {
        'children': {'links': {'self': "/parents/1/relationships/children",
                               'related': "/parents/1/children"}},
    }}
)


@responses.activate
def test_initialization():
    responses.add(responses.GET, f"{host}/parents/1",
                  json={'data': {'type': "parents", 'id': "1"}})
    parents = [Parent.get('1'),
               Parent(id='1'),
               {'data': {'type': "parents", 'id': '1'}},
               {'type': "parents", 'id': '1'}]
    children = [Child(relationships={'parent': parent})
                for parent in parents]
    assert all((children[i] == children[i + 1]
                for i in range(len(children) - 1)))
    assert all((children[i].__dict__ == children[i + 1].__dict__
                for i in range(len(children) - 1)))
    assert all((children[i].parent.__dict__ == children[i + 1].parent.__dict__
                for i in range(len(children) - 1)))

    child = Child(relationships={'parent': None})
    assert child.R == child.r == {'parent': None}


@responses.activate
def test_singular_fetch():
    responses.add(responses.GET, f"{host}/parents/1",
                  json={'data': parent_payloads[1]})

    child = Child(child_payloads[1])

    assert (child.R ==
            child.relationships ==
            {'parent': {'data': {'type': "parents", 'id': "1"},
                        'links': {'self': "/children/1/relationships/parent",
                                  'related': "/parents/1"}}})
    assert (child.r['parent'] ==
            child.related['parent'] ==
            child.parent ==
            Parent(id="1"))
    assert child.parent.a == child.parent.attributes == {}

    child.fetch('parent')

    assert len(responses.calls) == 1
    assert (child.r['parent'] ==
            child.related['parent'] ==
            child.parent ==
            Parent(id="1"))
    assert child.parent.a == child.parent.attributes == {'name': "parent 1"}
    assert child.parent.name == "parent 1"


@responses.activate
def test_fetch_plural():
    responses.add(responses.GET, f"{host}/parents/1/children",
                  json={'data': child_payloads[1:4],
                        'links': {'next': "/parents/1/children?page=2"}},
                  match_querystring=True)
    responses.add(responses.GET, f"{host}/parents/1/children?page=2",
                  json={'data': child_payloads[4:7],
                        'links': {'previous': "/parents/1/children?page=1"}},
                  match_querystring=True)

    parent = Parent(parent_payloads[1])
    assert 'children' not in parent.r
    parent.fetch('children')
    list(parent.children)

    assert len(responses.calls) == 1
    assert 'children' in parent.r
    assert len(parent.children) == 3
    assert isinstance(parent.children[0], Child)
    assert parent.children[1].id == "2"
    assert parent.children[2].name == "child 3"

    assert parent.children.has_next()
    assert not parent.children.has_previous()
    assert len(list(parent.children.all())) == 6


@responses.activate
def test_change_parent_with_save():
    response_body = deepcopy(child_payloads[1])
    relationship = response_body['relationships']['parent']
    relationship['data']['id'] = 2
    relationship['links']['related'] = relationship['links']['related'].\
        replace('1', '2')

    responses.add(responses.PATCH, f"{host}/children/1",
                  json={'data': response_body})

    child = Child(child_payloads[1])
    child.parent = Parent(parent_payloads[2])

    assert child.R['parent']['data']['id'] == "2"
    assert child.r['parent'].id == child.parent.id == "2"

    child.save()

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert (json.loads(call.request.body)['data']
            ['relationships']['parent']['data']['id'] ==
            "2")


@responses.activate
def test_change_parent_with_change():
    responses.add(responses.PATCH, f"{host}/children/1/relationships/parent")

    child = Child(child_payloads[1])
    new_parent = Parent(id="2")
    child.change('parent', new_parent)

    assert child.R['parent']['data']['id'] == "2"
    assert child.parent.id == "2"
    assert child.parent == new_parent

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert json.loads(call.request.body) == new_parent.as_relationship()


@responses.activate
def test_add():
    responses.add(responses.POST, f"{host}/parents/1/relationships/children")

    parent = Parent(parent_payloads[1])
    children = [Child(payload) for payload in child_payloads[1:4]]
    parent.add('children', [children[0],
                            children[1].as_relationship(),
                            children[2].as_resource_identifier()])

    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert (json.loads(call.request.body)['data'] ==
            [{'type': "children", 'id': str(i)} for i in range(1, 4)])
