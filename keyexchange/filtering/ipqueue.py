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
from collections import deque
import threading
import time


class IPQueue(object):
    """IP Queue that keeps a counter for each IP.

    When an IP comes in, it's append in the left and the counter
    initialized to 1.

    If the IP is already in the queue, its counter is incremented,
    and it's moved back to the left.

    When the queue is full, the right element is discarded.

    Elements that are too old gets discarded, so this works also
    for low traffic applications.
    """
    def __init__(self, maxlen=200, ttl=360):
        self._ips = deque()
        self._counter = dict()
        self._last_update = dict()
        self._maxlen = maxlen
        self._ttl = float(ttl)
        self._lock = threading.RLock()

    def __getstate__(self):
        odict = self.__dict__.copy()
        del odict['_lock']
        return odict

    def __setstate__(self, state):
        self.__dict__.update(state)

    def append(self, ip):
        """Adds the IP and raise the counter accordingly."""
        self._lock.acquire()
        try:
            if ip not in self._ips:
                self._ips.appendleft(ip)
                self._counter[ip] = 1
            else:
                self._ips.remove(ip)
                self._ips.appendleft(ip)
                self._counter[ip] += 1

            self._last_update[ip] = time.time()

            if len(self._ips) > self._maxlen:
                ip = self._ips.pop()
                del self._counter[ip]
                del self._last_update[ip]
        finally:
            self._lock.release()

    def _discard_if_old(self, ip):
        updated = self._last_update.get(ip)
        if updated is None:
            return False
        if time.time() - updated > self._ttl:
            self.remove(ip)
            return True
        return False

    def _discard_old_ips(self):
        # from right-to-left check the age and discard old ones
        index = len(self._ips) - 1
        while index >= 0:
            ip = self._ips[index]
            if not self._discard_if_old(ip):
                return
            index -= 1

    def count(self, ip):
        """Returns the IP count."""
        self._discard_if_old(ip)
        return self._counter.get(ip, 0)

    def __len__(self):
        self._discard_old_ips()
        return len(self._ips)

    def __contains__(self, ip):
        self._discard_if_old(ip)
        return ip in self._ips

    def remove(self, ip):
        self._lock.acquire()
        try:
            self._ips.remove(ip)
            del self._counter[ip]
            del self._last_update[ip]
        finally:
            self._lock.release()
