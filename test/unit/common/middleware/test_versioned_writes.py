# Copyright (c) 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import functools
import json
import os
import time
import unittest
from swift.common import swob
from swift.common.middleware import versioned_writes, copy
from swift.common.swob import Request
from test.unit.common.middleware.helpers import FakeSwift


class FakeCache(object):

    def __init__(self, val):
        if 'status' not in val:
            val['status'] = 200
        self.val = val

    def get(self, *args):
        return self.val


def local_tz(func):
    '''
    Decorator to change the timezone when running a test.

    This uses the Eastern Time Zone definition from the time module's docs.
    Note that the timezone affects things like time.time() and time.mktime().
    '''
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tz = os.environ.get('TZ', '')
        try:
            os.environ['TZ'] = 'EST+05EDT,M4.1.0,M10.5.0'
            time.tzset()
            return func(*args, **kwargs)
        finally:
            os.environ['TZ'] = tz
            time.tzset()
    return wrapper


class VersionedWritesBaseTestCase(unittest.TestCase):
    def setUp(self):
        self.app = FakeSwift()
        conf = {'allow_versioned_writes': 'true'}
        self.vw = versioned_writes.filter_factory(conf)(self.app)

    def call_app(self, req, app=None, expect_exception=False):
        if app is None:
            app = self.app

        self.authorized = []

        def authorize(req):
            self.authorized.append(req)

        if 'swift.authorize' not in req.environ:
            req.environ['swift.authorize'] = authorize

        req.headers.setdefault("User-Agent", "Marula Kruger")

        status = [None]
        headers = [None]

        def start_response(s, h, ei=None):
            status[0] = s
            headers[0] = h

        body_iter = app(req.environ, start_response)
        body = ''
        caught_exc = None
        try:
            for chunk in body_iter:
                body += chunk
        except Exception as exc:
            if expect_exception:
                caught_exc = exc
            else:
                raise

        if expect_exception:
            return status[0], headers[0], body, caught_exc
        else:
            return status[0], headers[0], body

    def call_vw(self, req, **kwargs):
        return self.call_app(req, app=self.vw, **kwargs)

    def assertRequestEqual(self, req, other):
        self.assertEqual(req.method, other.method)
        self.assertEqual(req.path, other.path)


class VersionedWritesTestCase(VersionedWritesBaseTestCase):
    def test_put_container(self):
        self.app.register('PUT', '/v1/a/c', swob.HTTPOk, {}, 'passed')
        req = Request.blank('/v1/a/c',
                            headers={'X-Versions-Location': 'ver_cont'},
                            environ={'REQUEST_METHOD': 'PUT'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')

        # check for sysmeta header
        calls = self.app.calls_with_headers
        method, path, req_headers = calls[0]
        self.assertEqual('PUT', method)
        self.assertEqual('/v1/a/c', path)
        self.assertTrue('x-container-sysmeta-versions-location' in req_headers)
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_container_allow_versioned_writes_false(self):
        self.vw.conf = {'allow_versioned_writes': 'false'}

        # PUT/POST container must fail as 412 when allow_versioned_writes
        # set to false
        for method in ('PUT', 'POST'):
            req = Request.blank('/v1/a/c',
                                headers={'X-Versions-Location': 'ver_cont'},
                                environ={'REQUEST_METHOD': method})
            status, headers, body = self.call_vw(req)
            self.assertEqual(status, "412 Precondition Failed")

        # GET performs as normal
        self.app.register('GET', '/v1/a/c', swob.HTTPOk, {}, 'passed')

        for method in ('GET', 'HEAD'):
            req = Request.blank('/v1/a/c',
                                headers={'X-Versions-Location': 'ver_cont'},
                                environ={'REQUEST_METHOD': method})
            status, headers, body = self.call_vw(req)
            self.assertEqual(status, '200 OK')

    def test_remove_versions_location(self):
        self.app.register('POST', '/v1/a/c', swob.HTTPOk, {}, 'passed')
        req = Request.blank('/v1/a/c',
                            headers={'X-Remove-Versions-Location': 'x'},
                            environ={'REQUEST_METHOD': 'POST'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')

        # check for sysmeta header
        calls = self.app.calls_with_headers
        method, path, req_headers = calls[0]
        self.assertEqual('POST', method)
        self.assertEqual('/v1/a/c', path)
        self.assertTrue('x-container-sysmeta-versions-location' in req_headers)
        self.assertEqual('',
                         req_headers['x-container-sysmeta-versions-location'])
        self.assertTrue('x-versions-location' in req_headers)
        self.assertEqual('', req_headers['x-versions-location'])
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_empty_versions_location(self):
        self.app.register('POST', '/v1/a/c', swob.HTTPOk, {}, 'passed')
        req = Request.blank('/v1/a/c',
                            headers={'X-Versions-Location': ''},
                            environ={'REQUEST_METHOD': 'POST'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')

        # check for sysmeta header
        calls = self.app.calls_with_headers
        method, path, req_headers = calls[0]
        self.assertEqual('POST', method)
        self.assertEqual('/v1/a/c', path)
        self.assertTrue('x-container-sysmeta-versions-location' in req_headers)
        self.assertEqual('',
                         req_headers['x-container-sysmeta-versions-location'])
        self.assertTrue('x-versions-location' in req_headers)
        self.assertEqual('', req_headers['x-versions-location'])
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_remove_add_versions_precedence(self):
        self.app.register(
            'POST', '/v1/a/c', swob.HTTPOk,
            {'x-container-sysmeta-versions-location': 'ver_cont'},
            'passed')
        req = Request.blank('/v1/a/c',
                            headers={'X-Remove-Versions-Location': 'x',
                                     'X-Versions-Location': 'ver_cont'},
                            environ={'REQUEST_METHOD': 'POST'})

        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertTrue(('X-Versions-Location', 'ver_cont') in headers)

        # check for sysmeta header
        calls = self.app.calls_with_headers
        method, path, req_headers = calls[0]
        self.assertEqual('POST', method)
        self.assertEqual('/v1/a/c', path)
        self.assertTrue('x-container-sysmeta-versions-location' in req_headers)
        self.assertTrue('x-remove-versions-location' not in req_headers)
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_get_container(self):
        self.app.register(
            'GET', '/v1/a/c', swob.HTTPOk,
            {'x-container-sysmeta-versions-location': 'ver_cont'}, None)
        req = Request.blank(
            '/v1/a/c',
            environ={'REQUEST_METHOD': 'GET'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertTrue(('X-Versions-Location', 'ver_cont') in headers)
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_get_head(self):
        self.app.register('GET', '/v1/a/c/o', swob.HTTPOk, {}, None)
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'GET'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

        self.app.register('HEAD', '/v1/a/c/o', swob.HTTPOk, {}, None)
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'HEAD'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_put_object_no_versioning(self):
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')

        cache = FakeCache({})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

    def test_put_object_post_as_copy(self):
        # PUTs due to a post-as-copy should NOT cause a versioning op
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, 'passed')

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100',
                     'swift.post_as_copy': True})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(1, self.app.call_count)

    def test_put_first_object_success(self):
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPNotFound, {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100',
                     'swift.trans_id': 'fake_trans_id'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(2, self.app.call_count)
        self.assertEqual(['VW', None], self.app.swift_sources)
        self.assertEqual({'fake_trans_id'}, set(self.app.txn_ids))

    def test_put_object_no_versioning_with_container_config_true(self):
        # set False to versions_write and expect no GET occurred
        self.vw.conf = {'allow_versioned_writes': 'false'}
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        called_method = [method for (method, path, hdrs) in self.app._calls]
        self.assertTrue('GET' not in called_method)

    def test_put_request_is_dlo_manifest_with_container_config_true(self):
        # set x-object-manifest on request and expect no versioning occurred
        # only the PUT for the original client request
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        req.headers['X-Object-Manifest'] = 'req/manifest'
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(1, self.app.call_count)

    def test_put_version_is_dlo_manifest_with_container_config_true(self):
        # set x-object-manifest on response and expect no versioning occurred
        # only initial GET on source object ok followed by PUT
        self.app.register('GET', '/v1/a/c/o', swob.HTTPOk,
                          {'X-Object-Manifest': 'resp/manifest'}, 'passed')
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(2, self.app.call_count)

    def test_delete_object_no_versioning_with_container_config_true(self):
        # set False to versions_write obviously and expect no GET versioning
        # container and GET/PUT called (just delete object as normal)
        self.vw.conf = {'allow_versioned_writes': 'false'}
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPNoContent, {}, 'passed')
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '204 No Content')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        called_method = \
            [method for (method, path, rheaders) in self.app._calls]
        self.assertTrue('PUT' not in called_method)
        self.assertTrue('GET' not in called_method)
        self.assertEqual(1, self.app.call_count)

    def test_new_version_success(self):
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPOk,
            {'last-modified': 'Thu, 1 Jan 1970 00:00:01 GMT'}, 'passed')
        self.app.register(
            'PUT', '/v1/a/ver_cont/001o/0000000001.00000', swob.HTTPCreated,
            {}, None)
        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100',
                     'swift.trans_id': 'fake_trans_id'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(['VW', 'VW', None], self.app.swift_sources)
        self.assertEqual({'fake_trans_id'}, set(self.app.txn_ids))

    def test_new_version_get_errors(self):
        # GET on source fails, expect client error response,
        # no PUT should happen
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPBadRequest, {}, None)
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '412 Precondition Failed')
        self.assertEqual(1, self.app.call_count)

        # GET on source fails, expect server error response
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPBadGateway, {}, None)
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '503 Service Unavailable')
        self.assertEqual(2, self.app.call_count)

    def test_new_version_put_errors(self):
        # PUT of version fails, expect client error response
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPOk,
            {'last-modified': 'Thu, 1 Jan 1970 00:00:01 GMT'}, 'passed')
        self.app.register(
            'PUT', '/v1/a/ver_cont/001o/0000000001.00000',
            swob.HTTPUnauthorized, {}, None)
        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '412 Precondition Failed')
        self.assertEqual(2, self.app.call_count)

        # PUT of version fails, expect server error response
        self.app.register(
            'PUT', '/v1/a/ver_cont/001o/0000000001.00000', swob.HTTPBadGateway,
            {}, None)
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '503 Service Unavailable')
        self.assertEqual(4, self.app.call_count)

    @local_tz
    def test_new_version_sysmeta_precedence(self):
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/c/o', swob.HTTPOk,
            {'last-modified': 'Thu, 1 Jan 1970 00:00:00 GMT'}, 'passed')
        self.app.register(
            'PUT', '/v1/a/ver_cont/001o/0000000000.00000', swob.HTTPOk,
            {}, None)

        # fill cache with two different values for versions location
        # new middleware should use sysmeta first
        cache = FakeCache({'versions': 'old_ver_cont',
                          'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'PUT', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

        # check that sysmeta header was used
        calls = self.app.calls_with_headers
        method, path, req_headers = calls[1]
        self.assertEqual('PUT', method)
        self.assertEqual('/v1/a/ver_cont/001o/0000000000.00000', path)

    def test_delete_first_object_success(self):
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET',
            '/v1/a/ver_cont?format=json&prefix=001o/&marker=&reverse=on',
            swob.HTTPNotFound, {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0', 'swift.trans_id': 'fake_trans_id'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(2, self.app.call_count)
        self.assertEqual(['VW', None], self.app.swift_sources)
        self.assertEqual({'fake_trans_id'}, set(self.app.txn_ids))

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('DELETE', '/v1/a/c/o'),
        ])

    def test_delete_latest_version_success(self):
        self.app.register(
            'GET',
            '/v1/a/ver_cont?format=json&prefix=001o/&marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}, '
            '{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}]')
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPCreated,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/2', swob.HTTPOk,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            headers={'X-If-Delete-At': 1},
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0', 'swift.trans_id': 'fake_trans_id'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(4, self.app.call_count)
        self.assertEqual(['VW', 'VW', 'VW', 'VW'], self.app.swift_sources)
        self.assertEqual({'fake_trans_id'}, set(self.app.txn_ids))

        # check that X-If-Delete-At was removed from DELETE request
        req_headers = self.app.headers[-1]
        self.assertNotIn('x-if-delete-at', [h.lower() for h in req_headers])

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/2'),
        ])

    def test_delete_single_version_success(self):
        # check that if the first listing page has just a single item then
        # it is not erroneously inferred to be a non-reversed listing
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET',
            '/v1/a/ver_cont?format=json&prefix=001o/&marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}]')
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/1', swob.HTTPOk,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/1', swob.HTTPOk,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', '/v1/a/ver_cont/001o/1'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/1'),
        ])

    def test_DELETE_on_expired_versioned_object(self):
        self.app.register(
            'GET',
            '/v1/a/ver_cont?format=json&prefix=001o/&marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}, '
            '{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}]')

        # expired object
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPNotFound,
            {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/1', swob.HTTPCreated,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPOk, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/1', swob.HTTPOk,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(5, self.app.call_count)

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('GET', '/v1/a/ver_cont/001o/1'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/1'),
        ])

    def test_denied_DELETE_of_versioned_object(self):
        authorize_call = []
        self.app.register(
            'GET',
            '/v1/a/ver_cont?format=json&prefix=001o/&marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}, '
            '{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}]')

        def fake_authorize(req):
            # the container GET is pre-auth'd so here we deny the object DELETE
            authorize_call.append(req)
            return swob.HTTPForbidden()

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'swift.authorize': fake_authorize,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '403 Forbidden')
        self.assertEqual(len(authorize_call), 1)
        self.assertRequestEqual(req, authorize_call[0])

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
        ])


class VersionedWritesOldContainersTestCase(VersionedWritesBaseTestCase):
    def test_delete_latest_version_success(self):
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}, '
            '{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}]')
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/'
            '&marker=001o/2',
            swob.HTTPNotFound, {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPCreated,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/2', swob.HTTPOk,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            headers={'X-If-Delete-At': 1},
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0', 'swift.trans_id': 'fake_trans_id'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(5, self.app.call_count)
        self.assertEqual(['VW', 'VW', 'VW', 'VW', 'VW'],
                         self.app.swift_sources)
        self.assertEqual({'fake_trans_id'}, set(self.app.txn_ids))

        # check that X-If-Delete-At was removed from DELETE request
        req_headers = self.app.headers[-1]
        self.assertNotIn('x-if-delete-at', [h.lower() for h in req_headers])

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', prefix_listing_prefix + 'marker=001o/2'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/2'),
        ])

    def test_DELETE_on_expired_versioned_object(self):
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}, '
            '{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}]')
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/'
            '&marker=001o/2',
            swob.HTTPNotFound, {}, None)

        # expired object
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPNotFound,
            {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/1', swob.HTTPCreated,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPOk, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/1', swob.HTTPOk,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '200 OK')
        self.assertEqual(len(self.authorized), 1)
        self.assertRequestEqual(req, self.authorized[0])
        self.assertEqual(6, self.app.call_count)

        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', prefix_listing_prefix + 'marker=001o/2'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('GET', '/v1/a/ver_cont/001o/1'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/1'),
        ])

    def test_denied_DELETE_of_versioned_object(self):
        authorize_call = []
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&reverse=on',
            swob.HTTPOk, {},
            '[{"hash": "x", '
            '"last_modified": "2014-11-21T14:14:27.409100", '
            '"bytes": 3, '
            '"name": "001o/1", '
            '"content_type": "text/plain"}, '
            '{"hash": "y", '
            '"last_modified": "2014-11-21T14:23:02.206740", '
            '"bytes": 3, '
            '"name": "001o/2", '
            '"content_type": "text/plain"}]')
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/'
            '&marker=001o/2',
            swob.HTTPNotFound, {}, None)
        self.app.register(
            'DELETE', '/v1/a/c/o', swob.HTTPForbidden,
            {}, None)

        def fake_authorize(req):
            authorize_call.append(req)
            return swob.HTTPForbidden()

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'swift.authorize': fake_authorize,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '403 Forbidden')
        self.assertEqual(len(authorize_call), 1)
        self.assertRequestEqual(req, authorize_call[0])
        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', prefix_listing_prefix + 'marker=001o/2'),
        ])

    def test_partially_upgraded_cluster(self):
        old_versions = [
            {'hash': 'etag%d' % x,
             'last_modified': "2014-11-21T14:14:%02d.409100" % x,
             'bytes': 3,
             'name': '001o/%d' % x,
             'content_type': 'text/plain'}
            for x in range(5)]

        # first container server can reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&reverse=on',
            swob.HTTPOk, {}, json.dumps(list(reversed(old_versions[2:]))))
        # but all objects are already gone
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/4', swob.HTTPNotFound,
            {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/3', swob.HTTPNotFound,
            {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPNotFound,
            {}, None)

        # second container server can't reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/2&reverse=on',
            swob.HTTPOk, {}, json.dumps(old_versions[3:]))

        # subsequent requests shouldn't reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&end_marker=001o/2',
            swob.HTTPOk, {}, json.dumps(old_versions[:1]))
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/0&end_marker=001o/2',
            swob.HTTPOk, {}, json.dumps(old_versions[1:2]))
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/1&end_marker=001o/2',
            swob.HTTPOk, {}, '[]')
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/1', swob.HTTPOk,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/1', swob.HTTPNoContent,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '204 No Content')
        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', '/v1/a/ver_cont/001o/4'),
            ('GET', '/v1/a/ver_cont/001o/3'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('GET', prefix_listing_prefix + 'marker=001o/2&reverse=on'),
            ('GET', prefix_listing_prefix + 'marker=&end_marker=001o/2'),
            ('GET', prefix_listing_prefix + 'marker=001o/0&end_marker=001o/2'),
            ('GET', prefix_listing_prefix + 'marker=001o/1&end_marker=001o/2'),
            ('GET', '/v1/a/ver_cont/001o/1'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/1'),
        ])

    def test_partially_upgraded_cluster_single_result_on_second_page(self):
        old_versions = [
            {'hash': 'etag%d' % x,
             'last_modified': "2014-11-21T14:14:%02d.409100" % x,
             'bytes': 3,
             'name': '001o/%d' % x,
             'content_type': 'text/plain'}
            for x in range(5)]

        # first container server can reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&reverse=on',
            swob.HTTPOk, {}, json.dumps(list(reversed(old_versions[-2:]))))
        # but both objects are already gone
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/4', swob.HTTPNotFound,
            {}, None)
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/3', swob.HTTPNotFound,
            {}, None)

        # second container server can't reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/3&reverse=on',
            swob.HTTPOk, {}, json.dumps(old_versions[4:]))

        # subsequent requests shouldn't reverse
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=&end_marker=001o/3',
            swob.HTTPOk, {}, json.dumps(old_versions[:2]))
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/1&end_marker=001o/3',
            swob.HTTPOk, {}, json.dumps(old_versions[2:3]))
        self.app.register(
            'GET', '/v1/a/ver_cont?format=json&prefix=001o/&'
            'marker=001o/2&end_marker=001o/3',
            swob.HTTPOk, {}, '[]')
        self.app.register(
            'GET', '/v1/a/ver_cont/001o/2', swob.HTTPOk,
            {'content-length': '3'}, None)
        self.app.register(
            'PUT', '/v1/a/c/o', swob.HTTPCreated, {}, None)
        self.app.register(
            'DELETE', '/v1/a/ver_cont/001o/2', swob.HTTPNoContent,
            {}, None)

        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/c/o',
            environ={'REQUEST_METHOD': 'DELETE', 'swift.cache': cache,
                     'CONTENT_LENGTH': '0'})
        status, headers, body = self.call_vw(req)
        self.assertEqual(status, '204 No Content')
        prefix_listing_prefix = '/v1/a/ver_cont?format=json&prefix=001o/&'
        self.assertEqual(self.app.calls, [
            ('GET', prefix_listing_prefix + 'marker=&reverse=on'),
            ('GET', '/v1/a/ver_cont/001o/4'),
            ('GET', '/v1/a/ver_cont/001o/3'),
            ('GET', prefix_listing_prefix + 'marker=001o/3&reverse=on'),
            ('GET', prefix_listing_prefix + 'marker=&end_marker=001o/3'),
            ('GET', prefix_listing_prefix + 'marker=001o/1&end_marker=001o/3'),
            ('GET', prefix_listing_prefix + 'marker=001o/2&end_marker=001o/3'),
            ('GET', '/v1/a/ver_cont/001o/2'),
            ('PUT', '/v1/a/c/o'),
            ('DELETE', '/v1/a/ver_cont/001o/2'),
        ])


class VersionedWritesCopyingTestCase(VersionedWritesBaseTestCase):
    # verify interaction of copy and versioned_writes middlewares

    def setUp(self):
        self.app = FakeSwift()
        conf = {'allow_versioned_writes': 'true'}
        self.vw = versioned_writes.filter_factory(conf)(self.app)
        self.filter = copy.filter_factory({})(self.vw)

    def call_filter(self, req, **kwargs):
        return self.call_app(req, app=self.filter, **kwargs)

    def test_copy_first_version(self):
        # no existing object to move to the versions container
        self.app.register(
            'GET', '/v1/a/tgt_cont/tgt_obj', swob.HTTPNotFound, {}, None)
        self.app.register(
            'GET', '/v1/a/src_cont/src_obj', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'PUT', '/v1/a/tgt_cont/tgt_obj', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/src_cont/src_obj',
            environ={'REQUEST_METHOD': 'COPY', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'},
            headers={'Destination': 'tgt_cont/tgt_obj'})
        status, headers, body = self.call_filter(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 2)
        self.assertEqual('GET', self.authorized[0].method)
        self.assertEqual('/v1/a/src_cont/src_obj', self.authorized[0].path)
        self.assertEqual('PUT', self.authorized[1].method)
        self.assertEqual('/v1/a/tgt_cont/tgt_obj', self.authorized[1].path)
        # note the GET on tgt_cont/tgt_obj is pre-authed
        self.assertEqual(3, self.app.call_count, self.app.calls)

    def test_copy_new_version(self):
        # existing object should be moved to versions container
        self.app.register(
            'GET', '/v1/a/src_cont/src_obj', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/a/tgt_cont/tgt_obj', swob.HTTPOk,
            {'last-modified': 'Thu, 1 Jan 1970 00:00:01 GMT'}, 'passed')
        self.app.register(
            'PUT', '/v1/a/ver_cont/007tgt_obj/0000000001.00000', swob.HTTPOk,
            {}, None)
        self.app.register(
            'PUT', '/v1/a/tgt_cont/tgt_obj', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/a/src_cont/src_obj',
            environ={'REQUEST_METHOD': 'COPY', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'},
            headers={'Destination': 'tgt_cont/tgt_obj'})
        status, headers, body = self.call_filter(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 2)
        self.assertEqual('GET', self.authorized[0].method)
        self.assertEqual('/v1/a/src_cont/src_obj', self.authorized[0].path)
        self.assertEqual('PUT', self.authorized[1].method)
        self.assertEqual('/v1/a/tgt_cont/tgt_obj', self.authorized[1].path)
        self.assertEqual(4, self.app.call_count)

    def test_copy_new_version_different_account(self):
        self.app.register(
            'GET', '/v1/src_a/src_cont/src_obj', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'GET', '/v1/tgt_a/tgt_cont/tgt_obj', swob.HTTPOk,
            {'last-modified': 'Thu, 1 Jan 1970 00:00:01 GMT'}, 'passed')
        self.app.register(
            'PUT', '/v1/tgt_a/ver_cont/007tgt_obj/0000000001.00000',
            swob.HTTPOk, {}, None)
        self.app.register(
            'PUT', '/v1/tgt_a/tgt_cont/tgt_obj', swob.HTTPCreated, {},
            'passed')
        cache = FakeCache({'sysmeta': {'versions-location': 'ver_cont'}})
        req = Request.blank(
            '/v1/src_a/src_cont/src_obj',
            environ={'REQUEST_METHOD': 'COPY', 'swift.cache': cache,
                     'CONTENT_LENGTH': '100'},
            headers={'Destination': 'tgt_cont/tgt_obj',
                     'Destination-Account': 'tgt_a'})
        status, headers, body = self.call_filter(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 2)
        self.assertEqual('GET', self.authorized[0].method)
        self.assertEqual('/v1/src_a/src_cont/src_obj', self.authorized[0].path)
        self.assertEqual('PUT', self.authorized[1].method)
        self.assertEqual('/v1/tgt_a/tgt_cont/tgt_obj', self.authorized[1].path)
        self.assertEqual(4, self.app.call_count)

    def test_copy_object_no_versioning_with_container_config_true(self):
        # set False to versions_write obviously and expect no extra
        # COPY called (just copy object as normal)
        self.vw.conf = {'allow_versioned_writes': 'false'}
        self.app.register(
            'GET', '/v1/a/src_cont/src_obj', swob.HTTPOk, {}, 'passed')
        self.app.register(
            'PUT', '/v1/a/tgt_cont/tgt_obj', swob.HTTPCreated, {}, 'passed')
        cache = FakeCache({'versions': 'ver_cont'})
        req = Request.blank(
            '/v1/a/src_cont/src_obj',
            environ={'REQUEST_METHOD': 'COPY', 'swift.cache': cache},
            headers={'Destination': '/tgt_cont/tgt_obj'})
        status, headers, body = self.call_filter(req)
        self.assertEqual(status, '201 Created')
        self.assertEqual(len(self.authorized), 2)
        self.assertEqual('GET', self.authorized[0].method)
        self.assertEqual('/v1/a/src_cont/src_obj', self.authorized[0].path)
        self.assertEqual('PUT', self.authorized[1].method)
        self.assertEqual('/v1/a/tgt_cont/tgt_obj', self.authorized[1].path)
        self.assertEqual(2, self.app.call_count)
