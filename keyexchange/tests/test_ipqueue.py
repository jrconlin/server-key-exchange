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

from keyexchange.filtering.ipqueue import IPQueue


class Worker(threading.Thread):
    def __init__(self, queue, ips):
        self.queue = queue
        self.ips = ips
        threading.Thread.__init__(self)

    def run(self):
        for i in range(100):
            for ip in self.ips:
                self.queue.append(ip)


class Remover(threading.Thread):
    def __init__(self, queue, ips):
        self.queue = queue
        self.ips = ips
        threading.Thread.__init__(self)

    def run(self):
        for i in range(100):
            for ip in self.ips:
                try:
                    self.queue.remove(ip)
                except ValueError:
                    pass


class TestIPQueue(unittest.TestCase):

    def test_ttl(self):
        # we want to discard IP that are in the queue for too long
        queue = IPQueue(ttl=.5)

        for ip in ('ip1', 'ip2', 'ip2', 'ip3', 'ip1'):
            queue.append(ip)

        self.assertEqual(queue.count('ip2'), 2)
        self.assertTrue('ip2' in queue)

        time.sleep(0.6)  # that kills all

        self.assertEqual(len(queue), 0)
        self.assertEqual(queue.count('ip2'), 0)

        for ip in ('ip1', 'ip2'):
            queue.append(ip)
        self.assertEqual(queue.count('ip2'), 1)

    def test_threading(self):
        # make sure the queue supports concurrency
        queue = IPQueue()
        workers = [Worker(queue, ['1', '2', '3']) for i in range(10)]
        removers = [Remover(queue, ['2', '3']) for i in range(10)]
        for worker in workers + removers:
            worker.start()

        for worker in workers + removers:
            worker.join()

        # if the queue is not thread-safe we would get less than 1000 here
        self.assertEqual(queue.count('1'), 1000)
