# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Sync Server
#
# The Initial Developer of the Original Code is the Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2010
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Tarek Ziade (tarek@mozilla.com)
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****
""" Functional test to simulate a JPake transaction.
"""
import unittest
import threading
import json
import time
import random
import hashlib
import os
import tempfile

from webtest import TestApp, AppError
from paste.deploy import loadapp

from keyexchange import wsgiapp
from keyexchange.tests.client import JPAKE
from keyexchange.util import MemoryClient


HERE = os.path.dirname(__file__)


class User(threading.Thread):

    def __init__(self, name, passwd, app, data=None, cid=None):
        threading.Thread.__init__(self)
        self.app = app
        if hasattr(app, 'root'):
            self.root = app.root
        else:
            self.root = ''
        self.name = name
        self.pake = JPAKE(passwd, signerid=name)
        self.data = data
        hash = hashlib.sha256(str(random.randint(1, 1000))).hexdigest()

        self.id = hash * 4
        if data is not None:
            res = self.app.get(self.root + '/new_channel',
                               headers={'X-KeyExchange-Id': self.id},
                               extra_environ=self.app.env)
            self.cid = str(json.loads(res.body))
        else:
            self.cid = cid
        self.curl = self.root + '/%s' % self.cid

    def _wait_data(self, etag=''):
        status = 304
        attempts = 0
        while status == 304 and attempts < 10:

            res = self.app.get(self.curl,
                               extra_environ=self.app.env,
                               headers={'If-None-Match': etag,
                                        'X-KeyExchange-Id': self.id})

            status = res.status_int
            attempts += 1
            if status == 304:
                time.sleep(.2)

        if status == 304:
            raise AssertionError('Failed to get next step')
        body = json.loads(res.body)

        def _clean(body):
            if isinstance(body, unicode):
                return str(body)
            res = {}
            for key, value in body.items():
                if isinstance(value, unicode):
                    value = str(value)
                elif isinstance(value, dict):
                    value = _clean(value)
                res[str(key)] = value
            return res
        return _clean(body)


class Sender(User):
    def run(self):
        headers = {'X-KeyExchange-Id': self.id}
        # step 1
        #print '%s sends step one' % self.name
        one = json.dumps(self.pake.one(), ensure_ascii=True)
        res = self.app.put(self.curl, params=one, headers=headers,
                           extra_environ=self.app.env)
        etag = res.headers['ETag']

        #print '%s now waits for step one from receiver' % self.name
        other_one = self._wait_data(etag)
        #print '%s received step one' % self.name

        # step 2
        #print '%s sends step two' % self.name
        two = json.dumps(self.pake.two(other_one))
        res = self.app.put(self.curl, params=two, headers=headers,
                           extra_environ=self.app.env)
        etag = res.headers['ETag']
        time.sleep(.2)

        # now wait for step 2 from the other iside
        other_two = self._wait_data(etag)
        #print '%s received step two from receiver' % self.name

        # then we build the key
        self.key = self.pake.three(other_two)

        # and we send the data (no crypting in the tests)
        #print '%s sends the data' % self.name
        data = json.dumps(self.data)
        res = self.app.put(self.curl, params=data, headers=headers,
                           extra_environ=self.app.env)


class Receiver(User):
    def run(self):
        headers = {'X-KeyExchange-Id': self.id}

        # waiting for step 1
        #print '%s waits for step one from sender' % self.name
        other_one = self._wait_data()

        # step 1
        #print '%s sends step one to receiver' % self.name
        one = json.dumps(self.pake.one(), ensure_ascii=True)
        res = self.app.put(self.curl, params=one, headers=headers,
                           extra_environ=self.app.env)
        etag = res.headers['ETag']

        # waiting for step 2
        #print '%s waits for step two from sender' % self.name
        other_two = self._wait_data(etag)

        # sending step 2
        #print '%s sends step two' % self.name
        two = json.dumps(self.pake.two(other_one))
        res = self.app.put(self.curl, params=two, headers=headers,
                           extra_environ=self.app.env)
        etag = res.headers['ETag']

        # then we build the key
        self.key = self.pake.three(other_two)

        # and we get the data (no crypting in the tests)
        self.data = self._wait_data(etag)
        #print '%s received the data' % self.name


class TestWsgiApp(unittest.TestCase):

    def setUp(self):
        ini_file = os.path.join(HERE, '..', '..', 'etc',
                                'tests.ini')
        app = loadapp('config:%s' % ini_file)
        # we don't test this here
        app.max_bad_request_calls = 100000

        from services.tests.support import create_test_app
        self.app = create_test_app(app)
        self.distant = os.environ.get('TEST_REMOTE') is not None

        if self.distant:
            self.filtering_middleware = None
            self.real_app = None
        else:
            self.filtering_middleware = self.app.app
            self.real_app = self.filtering_middleware.app
        self.app.env = self.env = {'REMOTE_ADDR': '127.0.0.1'}
        self._files = []

    def tearDown(self):
        for file_ in self._files:
            if os.path.exists(file_):
                os.remove(file_)

    def _tmpfile(self):
        fd, name = tempfile.mkstemp()
        self._files.append(name)
        return name

    def test_CORS(self):
        import pdb; pdb.set_trace();
        res = self.app._gen_request('OPTIONS', self.app.app.app.root + '/new_channel')
        


    def test_session(self):
        # we want to send data in a secure channel
        data = {'username': 'bob',
                'password': 'secret'}

        # let's create two end-points
        bob = Sender('Bob', 'secret', self.app, data)

        # bob creates a cid, sarah has to provide
        sarah = Receiver('Sarah', 'secret', self.app, cid=bob.cid)

        # bob starts
        bob.start()

        # let's wait a bit
        time.sleep(.5)

        # sarah starts next
        sarah.start()

        # let's wait for the transaction to end
        bob.join()
        sarah.join()

        # bob and sarah should have the same key
        self.assertEqual(bob.key, sarah.key)

        # sarah should have received the "encrypted" data from bob
        original_data = bob.data.items()
        original_data.sort()
        received_data = sarah.data.items()
        received_data.sort()
        self.assertEqual(original_data, received_data)

    def test_behavior(self):
        headers = {'X-KeyExchange-Id': 'b' * 256}

        # make sure we can't play with a channel that does not exist
        self.app.put('/boo', params='somedata', headers=headers, status=404,
                     extra_environ=self.env)
        self.app.get('/boo', status=404, headers=headers,
                     extra_environ=self.env)

        # testing the removal of a channel
        res = self.app.get('/new_channel', headers=headers,
                           extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid

        headers['X-KeyExchange-Cid'] = cid
        headers['X-KeyExchange-Log'] = 'some log'
        self.app.post('/report', headers=headers, extra_environ=self.env)
        del headers['X-KeyExchange-Cid']

        self.app.put(curl,  params='somedata', status=404, headers=headers,
                     extra_environ=self.env)
        self.app.get(curl, status=404, headers=headers,
                     extra_environ=self.env)

        if self.distant:
            return

        # let's try a really small ttl to make sure it works
        app = self.real_app

        if isinstance(app.cache, dict):
            # memory fallback, bye-bye
            return

        if isinstance(app.cache.cache, MemoryClient):
            return   # TTL is not implemented in the MemoryClient

        app.ttl = 1.
        res = self.app.get('/new_channel', headers=headers,
                           extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid
        self.app.put(curl,  params='somedata', status=200, headers=headers,
                     extra_environ=self.env)

        time.sleep(1.5)

        # should be dead now
        self.app.put(curl,  params='somedata', status=404, headers=headers,
                     extra_environ=self.env)

    def test_id_header(self):
        # all calls must be made with a unique 'X-KeyExchange-Id' header
        # this id must be of length 256

        # no id issues a 400
        self.app.get('/new_channel', status=400, extra_environ=self.env)

        # an id with the wrong size issues a 400
        headers = {'X-KeyExchange-Id': 'boo'}
        self.app.get('/new_channel', headers=headers, status=400,
                     extra_environ=self.env)

        # an id with the right size does the job
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', headers=headers,
                           extra_environ=self.env)
        cid = str(json.loads(res.body))

        # then we can put stuff as usual in the channel
        curl = '/%s' % cid
        self.app.put(curl, params='somedata', headers=headers,
                     status=200, extra_environ=self.env)

        # another id is used on the other side
        headers2 = {'X-KeyExchange-Id': 'c' * 256}
        self.app.get(curl,  headers=headers2, status=200,
                     extra_environ=self.env)

        # try to get the data with a different id and it's gone
        headers2 = {'X-KeyExchange-Id': 'e' * 256}
        self.app.get(curl,  headers=headers2, status=400,
                     extra_environ=self.env)

        # yes, gone..
        self.app.get(curl, status=404, headers=headers,
                     extra_environ=self.env)

        #
        # Testing with a bad id size
        #
        # an id with the right size does the job
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', headers=headers,
                           extra_environ=self.env)
        cid = str(json.loads(res.body))

        # then we can put stuff as usual in the channel
        curl = '/%s' % cid
        self.app.put(curl, params='somedata', headers=headers,
                     status=200, extra_environ=self.env)

        # another id is used on the other side
        headers2 = {'X-KeyExchange-Id': 'c' * 256}
        self.app.get(curl,  headers=headers2, status=200,
                     extra_environ=self.env)

        # try to get the data with a wrong id and it's gone
        headers2 = {'X-KeyExchange-Id': 'e' * 255}
        self.app.get(curl,  headers=headers2, status=400,
                     extra_environ=self.env)

        # yes, gone..
        self.app.get(curl, status=404, headers=headers,
                     extra_environ=self.env)

    def test_404s(self):
        # make sure other requests are issuing 404s
        for url in ('/some/url', '/UPER', '/o'):
            for method in ('get', 'put', 'post', 'delete'):
                getattr(self.app, method)(url, status=404,
                                          extra_environ=self.env)

        self.app.delete('/new_channel', status=405, extra_environ=self.env)

    def test_cef_logger(self):
        # creating a channel
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', headers=headers,
                           extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid
        logs = []

        def _counter(log, *args, **kw):
            logs.append(kw['msg'])

        # the channel is present
        self.app.get(curl, status=200, headers=headers,
                     extra_environ=self.env)

        # let's report a log message (and ask for deletion)
        old = wsgiapp.log_cef
        wsgiapp.log_cef = _counter
        try:
            headers['X-KeyExchange-Log'] = 'my log'
            headers['X-KeyExchange-Cid'] = cid
            self.app.post('/report', headers=headers, extra_environ=self.env)
        finally:
            wsgiapp.log_cef = old

        if not self.distant:
            self.assertEqual(logs[0].strip(), 'my log')

        # the channel should be gone
        self.app.get(curl, status=404, headers=headers,
                     extra_environ=self.env)

        # let's see if the real callback is correctly called
        if self.distant:
            return

        self.filtering_middleware.br_treshold = 2
        for i in range(2):
            try:
                self.app.get('/new_channel', extra_environ=self.env)
            except AppError:
                pass

    def test_cef_error(self):
        # creating a channel
        headers = {'X-KeyExchange-Id': 'b' * 256, 'User-Agent': '|'}
        res = self.app.get('/new_channel', status=200,
                           headers=headers, extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid

        headers['X-KeyExchange-Id'] = 'a' * 256
        self.app.get(curl, status=200, headers=headers,
                     extra_environ=self.env)

        # third actor should force channel delete
        headers['X-KeyExchange-Id'] = 'c' * 256
        try:
            self.app.get(curl, status=400, headers=headers,
                     extra_environ=self.env)
        except AppError:
            pass

        # channel should not exist anymore
        self.app.get(curl, status=404, headers=headers,
                     extra_environ=self.env)

    def test_report(self):
        logs = []

        def _counter(log, *args, **kw):
            logs.append(kw['msg'])

        headers = {'X-KeyExchange-Log': 'some'}
        old = wsgiapp.log_cef
        wsgiapp.log_cef = _counter
        try:
            self.app.post('/report', params='somelog', extra_environ=self.env)
            self.app.post('/report', params='more', extra_environ=self.env,
                          headers=headers)
        finally:
            wsgiapp.log_cef = old

        if self.distant:
            return

        self.assertEqual(logs[0], 'somelog')
        self.assertEqual(logs[1], 'some\nmore')

        # if there's an empty report, we just ignore this
        self.app.post('/report', status=200, extra_environ=self.env)

    def test_root(self):
        # the root must redirect to https://services.mozilla.com/
        res = self.app.get('/', status=301, extra_environ=self.env)
        self.assertEqual(res.location, 'https://services.mozilla.com')

        # the root also performs a health check on memcached.
        # if memcached fails to get/set/delete a test key,
        # a 503 is returned
        if self.distant:
            return

        self.real_app.cache.add = lambda x, y: False
        res = self.app.get('/', status=503, extra_environ=self.env)

    def test_max_gets(self):
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', status=200,
                           headers=headers, extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid

        # getting the etag
        res = self.app.put(curl, headers=headers, extra_environ=self.env,
                           params='xxx')
        headers2 = dict(headers)
        headers2['If-None-Match'] = res.headers['ETag']
        # this should not increment the counter (poll)
        # and generate a 304
        for i in range(4):
            self.app.get(curl, status=304, extra_environ=self.env,
                         headers=headers2)

        if not self.distant:
            cache = self.real_app.cache

        # 6 gets max !
        for i in range(6):
            self.app.get(curl, status=200, extra_environ=self.env,
                         headers=headers)

            if i < 5 and not self.distant:
                self.assertEqual(cache.get('GET:%s' % cid), str(i + 1))

        # the channel should be gone now
        self.app.get(curl, status=404, extra_environ=self.env,
                     headers=headers)

    def test_if_modified2(self):
        # creating a new channel
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', status=200,
                           headers=headers, extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid

        # client A puts some data
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     params='ooo')

        # ..ooops... client A had a timeout but the server received

        # client A tries again with a If-Match-None: *
        headers['If-None-Match'] = '*'
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     params='ooo', status=412)

    def test_if_modified(self):
        # creating a new channel
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', status=200,
                           headers=headers, extra_environ=self.env)
        cid = str(json.loads(res.body))
        curl = '/%s' % cid

        # client A puts some data
        # ..ooops... client A had a timeout and the server did not receive it
        # client A tries again with a If-Match-None: *
        headers['If-None-Match'] = '*'
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     params='ooo')
        del headers['If-None-Match']

        # client B gets it
        res = self.app.get(curl, headers=headers, extra_environ=self.env)

        # client B also keeps the etag from that data
        etag = res.headers['ETag']

        # client B put some data
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     params='xxx')

        # too bad...  client B had a timeout here !

        # and in the meantime client A did put some data
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     params='otherdata')

        # Client B retry with an If-Match header, with the etag
        # of the latest data he did GET from A
        headers['If-Match'] = etag

        # Client B gets a 412: it means, that the channel
        # has a different content than the last GET B received
        res = self.app.put(curl, headers=headers, extra_environ=self.env,
                           status=412)

        # Client B reads the ETag it got back
        #current_etag = res.headers['ETag']

        # so, IOW Client B latest PUT was successful.
        # let's GET again
        res = self.app.get(curl, headers=headers, extra_environ=self.env)

        # client B keeps the etag from that data
        etag = res.headers['ETag']

        # client B puts some data that never make it to
        # the server

        # client B try again with the If-Match
        headers['If-Match'] = etag
        self.app.put(curl, headers=headers, extra_environ=self.env,
                     status=200)

        # success !

    def test_new_channel_header(self):
        headers = {'X-KeyExchange-Id': 'b' * 256}
        res = self.app.get('/new_channel', status=200,
                           headers=headers, extra_environ=self.env)
        cid = str(json.loads(res.body))

        # checking that the header is also present
        self.assertEqual(res.headers['X-KeyExchange-Channel'], cid)

    def test_new_channel_observe_mode_cef(self):

        logfile = self._tmpfile()

        # we want to work in observe mode
        config = {'keyexchange.use_memory': True,
                  'filtering.use': True,
                  'filtering.treshold': 1,
                  'filtering.use_memory': True,
                  'filtering.observe': True,
                  'cef.use': True,
                  'cef.file': logfile,
                  'cef.version': '1',
                  'cef.vendor': 'moz',
                  'cef.device_version': '1',
                  'cef.product': 'keyex'}

        app = wsgiapp.make_app(config)
        test_app = TestApp(app)
        test_app.env = self.env = {'REMOTE_ADDR': '127.0.0.1'}

        key = ("0987507938961303790993045438290584563289655491418028073179828"
               "34980265161474647d3361865e53d1d7c5a1fa70481fe988c34a29e4acfaf"
               "b16362dc0b73905a207bfed485d6e52ebefd14c26cb79247f24e5dfaf931e"
               "574c39acdecba375e81a560b00a311765cc02b5a4d01804955e7256317418"
               "af1cc1fb84ae123123")

        headers = {'X-KeyExchange-Id': key}

        # let's do one call (id too long)
        test_app.get('/new_channel', status=400,
                     headers=headers, extra_environ=self.env)

        # we should be blacklisted now. but in observe mode,
        # life continues
        for i in range(10):
            test_app.get('/new_channel', status=400,
                         headers=headers, extra_environ=self.env)

        headers = {'X-KeyExchange-Id': 'x' * 256}
        test_app.get('/new_channel', status=200,
                     headers=headers, extra_environ=self.env)

        # now let's check the CEF logs, we should have only
        # one 'blacklisted' event, followed by 11 bad requests events.
        with open(logfile) as f:
            logs = [line.split('|')[4]
                    for line in f.read().split('\n')
                    if line.strip() != '']

        self.assertEqual(logs.count('BlackListed IP'), 1)
        self.assertEqual(logs.count('Invalid X-KeyExchange-Id'), 11)
