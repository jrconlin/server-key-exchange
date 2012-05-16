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
import unittest
import time
import threading
import random
import cPickle

from keyexchange.filtering.middleware import IPFiltering
from keyexchange.filtering.blacklist import Blacklist
from keyexchange.filtering.ipqueue import IPQueue
from keyexchange.util import MemoryClient

from webtest import TestApp, AppError
from webob.exc import HTTPForbidden


class FakeApp(object):
    def __call__(self, environ, start_response):
        path = environ['PATH_INFO']
        if 'boo' in path:
            start_response('400 Bad Request',
                           [('Content-Type', 'text/plain')])
            return ['400 Bad Request', 'error']
        else:
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return ['something', 'valid']


class TestIPFiltering(unittest.TestCase):

    def setUp(self):
        # this setting will blacklist an IP that does more than 5 calls
        if random.randrange(2) == 1:
            app = IPFiltering(FakeApp(), queue_size=10, blacklist_ttl=.5,
                              treshold=5, br_queue_size=3,
                              br_blacklist_ttl=.5, use_memory=True,
                              ip_whitelist=['192.168/16', '127.0/8', '10/8'])
        else:
            app = IPFiltering(FakeApp(), queue_size=10, blacklist_ttl=.5,
                              treshold=5, br_queue_size=3,
                              br_blacklist_ttl=.5, use_memory=True,
                              ip_whitelist=['192.168/16', '127.0/8', '10/8'],
                              async=False, update_blfreq=2)

        self.app = TestApp(app)

    def test_reached_max_observe(self):
        self.app.app.observe = True
        env = {'REMOTE_ADDR': 'bad_guy'}

        # no ip, no chocolate
        try:
            self.app.get('/', status=403)
        except HTTPForbidden:
            pass

        # doing 5 calls
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        # the next call should *NOT* be rejected
        self.app.get('/', status=200, extra_environ=env)

    def test_reached_max(self):
        env = {'REMOTE_ADDR': '193.0.0.1'}

        # no ip, no chocolate
        try:
            self.app.get('/', status=403)
        except HTTPForbidden:
            pass

        # doing 5 calls
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        # the next call should be rejected
        try:
            self.app.get('/', status=403, extra_environ=env)
        except HTTPForbidden:
            pass

        # TTL test - we make the assumption that the beginning of the
        # test took less than 1.5s

        # we should be on track now
        time.sleep(1.5)
        self.app.get('/', status=200, extra_environ=env)

    def test_reached_br_max(self):
        self.app.app.br_treshold = 3
        env = {'HTTP_X_FORWARDED_FOR': '167.0.0.1, 10.1.1.2, 10.12.12.1'}

        # doing 3 calls
        for i in range(3):
            self.assertRaises(AppError, self.app.get, '/boo', status=200,
                              extra_environ=env)

        # the next call should be rejected
        try:
            self.app.get('/', status=403, extra_environ=env)
        except HTTPForbidden:
            pass

        # TTL test - we make the assumption that the beginning of the
        # test took less than 1.5s

        # we should be on track now
        time.sleep(1.5)
        self.app.get('/', status=200, extra_environ=env)

    def test_basics(self):
        app = self.app.app
        app.br_treshold = app.treshold = 100000

        # saturating the queue now to make sure its LRU-ing right
        for i in range(15):
            env = {'REMOTE_ADDR': str(i)}
            try:
                self.app.get('/', extra_environ=env)
            except HTTPForbidden:
                pass

        self.assertEqual(len(app._last_ips), 10)
        env = {'REMOTE_ADDR': '127.0.0.2'}

        for i in range(15):
            env = {'REMOTE_ADDR': str(i)}
            try:
                self.app.get('/boo', extra_environ=env)
            except (AppError, HTTPForbidden):
                pass

        # let's see how's the queue is doing
        self.assertEqual(len(app._last_br_ips), 3)

    def test_blacklist_thread_safe(self):
        # testing the thread-safeness of Blacklist
        cache = MemoryClient(None)
        blacklist = Blacklist(cache)

        class Worker(threading.Thread):
            def __init__(self, name, blacklist):
                self.blacklist = blacklist
                threading.Thread.__init__(self)

            def run(self):
                # we want to:
                #   - load the list
                #   - add 10 elements
                #   - remove 1
                #   - save the list
                self.blacklist.update()

                for i in range(10):
                    self.blacklist.add(self.name + str(i))

                # remove a random element
                ips = list(self.blacklist.ips)
                self.blacklist.remove(random.choice(ips))

                # save the list
                self.blacklist.save()

        workers = [Worker(str(i), blacklist) for i in range(10)]
        for worker in workers:
            worker.start()

        for worker in workers:
            worker.join()

        # we should have 90 elements
        self.assertEqual(len(blacklist), 90)
        self.assertFalse(blacklist._dirty)

    def test_admin_page(self):
        # activate the admin page
        self.app.app.admin_page = '/__admin__'
        res = self.app.get('/__admin__')
        self.assertFalse('myip' in res.body)

        env = {'REMOTE_ADDR': 'myip'}

        # doing 5 calls
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        # the next call should be rejected
        try:
            self.app.get('/', status=403, extra_environ=env)
        except HTTPForbidden:
            pass

        # and the admin page should display the IP
        res = self.app.get('/__admin__')
        self.assertTrue('myip' in res.body)

        # and the IP should be in the queue
        self.assertTrue('myip' in self.app.app._last_ips)

        # let's remove the IP from the blacklist
        res.form['myip'].checked = True
        res.form.submit()

        # and the IP should be gone
        res = self.app.get('/__admin__')
        self.assertTrue('myip' not in res.body)

        # and the IP should also be removed from the IP queues
        self.assertTrue('myip' not in self.app.app._last_ips)
        self.assertTrue('myip' not in self.app.app._last_br_ips)

    def test_ip_whitelist(self):
        env = {'REMOTE_ADDR': '127.0.0.1'}

        # doing 5 calls
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        # they should be discarded
        self.assertTrue('127.0.0.1' not in self.app.app._last_ips)
        self.assertTrue('127.0.0.1' not in self.app.app._last_br_ips)

    def test_blacklist_dies(self):
        # testing the thread-safeness of Blacklist
        cache = MemoryClient(None)
        blacklist = Blacklist(cache)

        def raiseit():
            raise ValueError()

        blacklist.save = blacklist.update = raiseit
        # make sure the logging happens and the thread does not die
        time.sleep(0.5)

    def test_sync(self):
        app = IPFiltering(FakeApp(), queue_size=10, blacklist_ttl=.5,
                          treshold=5, br_queue_size=3,
                          br_blacklist_ttl=.5, use_memory=True,
                          ip_whitelist=['192.168/16', '127.0/8', '10/8'],
                           async=False, update_blfreq=2)

        # make sure the bl is getting updated on synchronous mode
        counter = [0]

        def _incr():
            counter[0] += 1

        old_save = app._blacklisted.save
        old_update = app._blacklisted.update
        app._blacklisted.save = app._blacklisted.update = _incr
        env = {'REMOTE_ADDR': '127.0.0.1'}
        web_app = TestApp(app)

        try:
            # doing 10 calls
            for i in range(5):
                web_app.get('/', status=200, extra_environ=env)
        finally:
            app._blacklisted.save = old_save
            app._blacklisted.update = old_update

        self.assertEqual(counter[0], 2)

    def test_pickling(self):
        queue = IPQueue()
        queue.append('one')
        pickled = cPickle.dumps(queue)
        queue2 = cPickle.loads(pickled)
        self.assertEqual(queue2.count('one'), 1)

        cache = MemoryClient(None)
        blacklist = Blacklist(cache)
        blacklist.add('ip')
        pickled = cPickle.dumps(blacklist)
        bl2 = cPickle.loads(pickled)
        self.assertTrue('ip' in bl2)

    def test_observe_blacklist(self):
        self.app.app.observe = True
        env = {'REMOTE_ADDR': 'ok'}

        # keeping track of the calls
        count = [0]

        def _inc(*args):
            count[0] += 1

        self.app.app.callback = _inc

        # doing 5 calls
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        self.assertEqual(count[0], 1)

        # we should be blacklisted now, but should still work
        for i in range(5):
            self.app.get('/', status=200, extra_environ=env)

        # but we don't want to callback in case the ip is
        # blacklisted in observe mode
        self.assertEqual(count[0], 1)
