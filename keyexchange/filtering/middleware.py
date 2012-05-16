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
"""
IP Filtering middleware. This middleware will:

- Reject all new attempts made by an IP, if this IP already made too many
  attempts.
- Reject IPs are are making too many bad requests

To perform this, we keep a LRU of the last N ips in memory and increment the
calls. If an IP as a high number of calls, it's blacklisted.

For the bad request counter, the same technique is used.

Blacklisted IPs are kept in memory with a TTL.
"""
import os
import cgi

from mako.template import Template

from keyexchange.filtering.IPy import IP
from keyexchange.util import get_memcache_class
from keyexchange.filtering.blacklist import Blacklist
from keyexchange.filtering.ipqueue import IPQueue


class IPFiltering(object):
    """Filtering IPs
    """
    def __init__(self, app, blacklist_ttl=300, br_blacklist_ttl=86400,
                 queue_size=200, br_queue_size=20, treshold=20,
                 br_treshold=5, cache_servers=['127.0.0.0.1:11211'],
                 admin_page=None, use_memory=False, refresh_frequency=1,
                 observe=False, callback=None, ip_whitelist=None,
                 async=True, update_blfreq=None, ip_queue_ttl=360,
                 br_callback=None):

        """Initializes the middleware.

        - app: the wsgi application the middleware wraps
        - blacklist_ttl: defines how long in seconds an IP is blacklisted
        - br_blacklist_ttl: defines how long in seconds an IP that did too many
          bad requests is blacklisted
        - queue_size: Size of the queue used to keep track of the last callers
        - br_queue_size: Size of the queue used to keep track of the last
          callers that provokated a bad request.
        - treshold: max number of calls per IP before we blacklist it.
        - br_treshold: max number of bad request per IP before we blacklist it.
        - observe: if set to True, IPs are still blacklisted but not rejected.
          This mode is useful to observe the behavior of an application without
          rejecting any call, to make sure a configuration works fine. Notice
          that a blacklisted IP will continue to raise its counter in the
          queue.
        - callback: callable that will be called with an IP that is added
          in the blacklist.
        - br_callback: callable that will be called when a bad request is
          caught.
        - ip_whitelist: a list of IP that should never be blacklisted.
          Supports all netmask notations.
        - async: if True, uses a thread to sync the blacklist. Otherwise
          updates it every update_blfreq requests.
        - update_blfreq: number of requests before the blacklist is updated.
          async must be False.
        - ip_queue_ttl: Maximum time to live for an IP in the queues.
        """
        self.app = app
        self.blacklist_ttl = blacklist_ttl
        self.br_blacklist_ttl = br_blacklist_ttl
        self.queue_size = queue_size
        self.br_queue_size = br_queue_size
        self.treshold = treshold
        self.br_treshold = br_treshold
        self.observe = observe
        self._last_ips = IPQueue(queue_size, ttl=ip_queue_ttl)
        self._last_br_ips = IPQueue(br_queue_size, ttl=ip_queue_ttl)
        if isinstance(cache_servers, str):
            cache_servers = [cache_servers]
        self._cache_server = get_memcache_class(use_memory)(cache_servers)
        self.async = async
        if self.async and update_blfreq is not None:
            raise ValueError('Cannot use async mode with update_blfreq')
        self.update_blfreq = update_blfreq
        self._blcounter = 0
        self._blacklisted = Blacklist(self._cache_server, refresh_frequency,
                                      self.async)
        if admin_page is not None and not admin_page.startswith('/'):
            admin_page = '/' + admin_page
        self.admin_page = admin_page
        admin_mako = os.path.join(os.path.dirname(__file__), 'admin.mako')
        self._admin_tpl = Template(filename=admin_mako)
        self.callback = callback
        self.br_callback = br_callback

        if ip_whitelist is None:
            self.ip_whitelist = []
        else:
            self.ip_whitelist = [IP(ip) for ip in ip_whitelist]

    def _is_whitelisted(self, ip):
        for ip_range in self.ip_whitelist:
            try:
                if ip in ip_range:
                    return True
            except ValueError:
                # happens when the IP is unparseable
                return False
        return False

    def _check_ip(self, ip, environ):
        if self._is_whitelisted(ip):
            return

        # in observe mode we want to check if the ip is not
        # already blacklisted before we treat it
        # If it's blacklisted, we don't even want to add it
        # in the queue.
        if self.observe and ip in self._blacklisted:
            return

        # insert the IP in the queue
        # if the queue is full, the opposite-end item is discarded
        self._last_ips.append(ip)

        # counts its ratio in the queue
        if self._last_ips.count(ip) >= self.treshold:

            # blacklisting the IP
            self._blacklisted.add(ip, self.blacklist_ttl)
            if self.callback is not None:
                self.callback(ip, environ)

    def _inc_bad_request(self, ip, environ):
        if self._is_whitelisted(ip):
            return

        if self.br_callback is not None:
                self.br_callback(ip, environ)

        if self.observe and ip in self._blacklisted:
            return
        # insert the IP in the br queue
        # if the queue is full, the opposite-end item is discarded
        self._last_br_ips.append(ip)

        # counts its occurences in the queue
        if self._last_br_ips.count(ip) >= self.br_treshold:
            # blacklisting the IP
            self._blacklisted.add(ip, self.br_blacklist_ttl)
            if self.callback is not None:
                self.callback(ip, environ)

    def admin(self, environ, start_response):
        """Displays an admin page containing the blacklisted IPs

        This page is not activated by default.
        """
        post_env = environ.copy()
        post_env['QUERY_STRING'] = ''
        post = cgi.FieldStorage(fp=environ['wsgi.input'], environ=post_env,
                                keep_blank_values=True)
        ips = [ip for ip in post.keys() if post[ip].value == 'on']
        for ip in ips:
            try:
                self._blacklisted.remove(ip)
                self._blacklisted.save()  # force immediate save
                if ip in self._last_ips:
                    self._last_ips.remove(ip)
                if ip in self._last_br_ips:
                    self._last_br_ips.remove(ip)
            except KeyError:
                pass

        headers = [('Content-Type', 'text/html')]
        start_response('200 OK', headers)
        # we want to display the list of blacklisted IPs
        return [self._admin_tpl.render(ips=self._blacklisted.ips,
                                       admin_page=self.admin_page,
                                       observe=self.observe)]

    def __call__(self, environ, start_response):
        # is it an admin call ?
        url = environ.get('PATH_INFO')
        if url is not None and url == self.admin_page:
            return self.admin(environ, start_response)

        start_response_status = []

        def _start_response(status, headers, exc_info=None):
            start_response_status.append(status)
            return start_response(status, headers, exc_info)

        # what's the remote ip ?
        if 'HTTP_X_FORWARDED_FOR' in environ:
            ip = environ['HTTP_X_FORWARDED_FOR'].split(',')[0].strip()
        elif 'REMOTE_ADDR' in environ:
            ip = environ['REMOTE_ADDR']
        else:
            ip = None

        if ip is None or (ip in self._blacklisted and not self.observe):
            # returning a 403
            headers = [('Content-Type', 'text/plain')]
            start_response('403 Forbidden', headers)
            return ["Forbidden: You don't have permission to access"]

        # updating the blacklist on sync mode
        if not self.async:
            self._blcounter += 1
            if self._blcounter >= self.update_blfreq:
                self._blcounter = 0
                try:
                    if self._blacklisted.outsynced:
                        self._blacklisted.save()
                    else:
                        self._blacklisted.update()
                except Exception, e:
                    from keyexchange.filtering import logger
                    logger.error(str(e))

        # checking for the IP in our counter
        self._check_ip(ip, environ)

        res = self.app(environ, _start_response)

        if start_response_status[0].startswith('400'):
            # this IP issued a 400. We want to log that
            self._inc_bad_request(ip, environ)

        return res
