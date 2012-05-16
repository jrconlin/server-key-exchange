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
""" Various helpers.
"""
import json
from webob import Response
from services.util import randchar


CID_CHARS = '23456789abcdefghijkmnpqrstuvwxyz'


def json_response(data, dump=True, **kw):
    """Returns Response containing a json string"""
    if dump:
        data = json.dumps(data)
    return Response(data, content_type='application/json', **kw)


def generate_cid(size=4):
    """Returns a random channel id."""
    return ''.join([randchar(CID_CHARS) for i in range(size)])


class MemoryClient(dict):
    """Fallback if a memcache client is not installed.
    """
    def __init__(self, servers):
        pass

    def set(self, key, value, time=0):
        self[key] = value
        return True

    cas = set

    def add(self, key, value, time=0):
        if key in self:
            return False
        self[key] = value
        return True

    def replace(self, key, value, time=0):
        if key not in self:
            return False
        self[key] = value
        return True

    def delete(self, key):
        if not key in self:
            return True  # that's how memcache libs do...
        del self[key]
        return True

    def incr(self, key):
        val = self[key]
        self[key] = str(int(val) + 1)


class PrefixedCache(object):
    def __init__(self, cache, prefix=''):
        self.cache = cache
        self.prefix = ''

    def incr(self, key):
        return self.cache.incr(self.prefix + key)

    def get(self, key):
        return self.cache.get(self.prefix + key)

    def set(self, key, value, **kw):
        return self.cache.set(self.prefix + key, value, **kw)

    def delete(self, key):
        return self.cache.delete(self.prefix + key)

    def add(self, key, value, **kw):
        return self.cache.add(self.prefix + key, value, **kw)


def get_memcache_class(memory=False):
    """Returns the memcache class."""
    if memory:
        return MemoryClient
    import memcache
    return memcache.Client
