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
KeyExchange server - see https://wiki.mozilla.org/Services/Sync/SyncKey/J-PAKE
"""
import re
from hashlib import md5
import time
import random
import json
import sys
import copy

from webob.dec import wsgify
from webob.exc import (HTTPNotModified, HTTPNotFound, HTTPServiceUnavailable,
                       HTTPBadRequest, HTTPMethodNotAllowed,
                       HTTPMovedPermanently, HTTPPreconditionFailed)

from cef import log_cef
from services.config import Config

from keyexchange.util import (generate_cid, json_response, CID_CHARS,
                              PrefixedCache, get_memcache_class)
from keyexchange.filtering import IPFiltering


_URL = re.compile('^/(new_channel|report|[%s]+)/?$' % CID_CHARS)
_CPREFIX = 'keyexchange:'
_INC_KEY = '%schannel_id' % _CPREFIX
_EMPTY = '{}'


def _cid2str(cid):
    if cid is None:
        return 'EMPTY'
    return cid


class KeyExchangeApp(object):


    """ These MUST include ALL headers exchanged. They may be 
        divided up into Inbound (returned via OPTIONS method)
        and Outbound (returned as part of the Response). 
        Failing to include a header will result in the user 
        agent rejecting the data.
    """
    CORS_HEADERS = [('Access-Control-Allow-Origin','*'), 
                ('Access-Control-Allow-Headers', 
                    ', '.join(['contenttype',
                        'x-keyexchange-cid',
                        'x-keyexchange-channel',
                        'x-keyexchange-id',
                        'x-keyexchange-log',
                        'if-match',
                        'if-none-match'])),
                ('Access-Control-Expose-Headers', 
                    ', '.join(['etag',
                        'x-status'])),
                ('Access-Control-Allow-Methods', 
                    ', '.join(['GET',
                        'POST', 
                        'PUT',
                        'OPTIONS']))]


    def __init__(self, config):
        self.config = config
        self.cid_len = config.get('keyexchange.cid_len', 4)
        self.ttl = config.get('keyexchange.ttl', 300)
        self.max_gets = config.get('keyexchange.max_gets', 6)
        self.root = self.config.get('keyexchange.root_redirect')
        servers = config.get('keyexchange.cache_servers', ['127.0.0.1:11211'])
        """ 
            * Allow any origin
            * Must list explicit headers to allow (case insensitive)
                * Any header included, but not listed will fail the result
            * Likewise, list all methods to be used.
        """
        if isinstance(servers, str):
            self.cache_servers = [servers]
        else:
            self.cache_servers = servers
        use_memory = config.get('keyexchange.use_memory', False)
        cache = get_memcache_class(use_memory)(self.cache_servers)
        self.cache = PrefixedCache(cache, _CPREFIX)

    def _get_new_cid(self, client_id):
        tries = 0
        ttl = time.time() + self.ttl
        content = ttl, [client_id], _EMPTY, None

        while tries < 100:
            new_cid = generate_cid(self.cid_len)
            if self.cache.get(new_cid) is not None:
                tries += 1
                continue   # already taken

            success = self.cache.add(new_cid, content, time=ttl)
            if success:
                break
            tries += 1

        if not success:
            raise HTTPServiceUnavailable()

        return new_cid

    def _health_check(self):
        """Checks that memcache is up and works as expected"""
        rand = ''.join([random.choice('abcdefgh1234567') for i in range(50)])
        key = 'test_%s' % rand
        success = self.cache.add(key, 'test')
        if not success:
            raise HTTPServiceUnavailable()
        stored = self.cache.get(key)
        if stored != 'test':
            raise HTTPServiceUnavailable()
        self.cache.delete(key)
        stored = self.cache.get(key)
        if stored is not None:
            raise HTTPServiceUnavailable()

    @wsgify
    def __call__(self, request):
        if request.method == 'OPTIONS':
            sys.stderr.write("###OPTIONS: \n");
            for h in self.CORS_HEADERS:
                sys.stderr.write("   %s: %s\n" % h);
            # Trace to see if this is actually setting the headers...
            return json_response('',
                headerlist = copy.deepcopy(self.CORS_HEADERS));
        request.config = self.config
        client_id = request.headers.get('X-KeyExchange-Id')
        method = request.method
        url = request.path_info

        # the root does a health check on memcached, then
        # redirects to services.mozilla.com
        if url == '/':
            if method != 'GET':
                raise HTTPMethodNotAllowed()
            self._health_check()
            raise HTTPMovedPermanently(location=self.root)

        match = _URL.match(url)
        if match is None:
            raise HTTPNotFound()

        url = match.group(1)
        if url == 'new_channel':
            # creation of a channel
            if method != 'GET':
                raise HTTPMethodNotAllowed()
            if not self._valid_client_id(client_id):
                # The X-KeyExchange-Id is valid
                try:
                    log = 'Invalid X-KeyExchange-Id'
                    log_cef(log, 5, request.environ, self.config,
                            msg=_cid2str(client_id))
                finally:
                    raise HTTPBadRequest()
            cid = self._get_new_cid(client_id)
            headers = [('X-KeyExchange-Channel', cid),
                       ('Content-Type', 'application/json')]
            headers.extend(self.CORS_HEADERS)
            return json_response(cid, headerlist=headers)

        elif url == 'report':
            if method != 'POST':
                raise HTTPMethodNotAllowed()
            return self.report(request, client_id)

        # validating the client id - or registering id #2
        channel_content = self._check_client_id(url, client_id, request)

        # actions are dispatched in this class
        sys.stderr.write('calling ' + method + "\n");
        method = getattr(self, '%s_channel' % method.lower(), None)
        if method is None:
            sys.stderr.write("not found\n");
            raise HTTPNotFound()

        return method(request, url, channel_content)

    def _valid_client_id(self, client_id):
        return client_id is not None and len(client_id) == 256

    def _check_client_id(self, channel_id, client_id, request):
        """Registers the client id into the channel.

        If there are already two registered ids, the channel is closed
        and we send back a 400. Also returns the new channel content.
        """
        if not self._valid_client_id(client_id):
            # the key is invalid
            try:
                log = 'Invalid X-KeyExchange-Id'
                log_cef(log, 5, request.environ, self.config,
                        msg=_cid2str(client_id))
            finally:
                # we need to kill the channel
                if not self._delete_channel(channel_id):
                    log_cef('Could not delete the channel', 5,
                            request.environ, self.config,
                            msg=_cid2str(channel_id))

                raise HTTPBadRequest()

        content = self.cache.get(channel_id)
        if content is None:
            # we have a valid channel id but it does not exists.
            log = 'Invalid X-KeyExchange-Channel'
            log_cef(log, 5, request.environ, self.config,
                    _cid2str(channel_id))
            raise HTTPNotFound()

        ttl, ids, data, etag = content
        if len(ids) < 2:
            # first or second id, if not already registered
            if client_id in ids:
                return content   # already registered
            ids.append(client_id)
        else:
            # already full, so either the id is present, either it's a 3rd one
            if client_id in ids:
                return  content  # already registered

            # that's an unknown id, hu-ho
            try:
                log = 'Unknown X-KeyExchange-Id'
                log_cef(log, 5, request.environ, self.config,
                        msg=_cid2str(client_id))
            finally:
                if not self._delete_channel(channel_id):
                    log_cef('Could not delete the channel', 5,
                            request.environ, self.config,
                            msg=_cid2str(channel_id))

                raise HTTPBadRequest()

        content = ttl, ids, data, etag

        # looking good
        if not self.cache.set(channel_id, content, time=ttl):
            raise HTTPServiceUnavailable()
        return content

    def _etag(self, data):
        return md5(data).hexdigest()

    def _etag_match(self, etag, header):
        if not hasattr(header, 'etags'):
            return False
        return etag in getattr(header, 'etags')

    def put_channel(self, request, channel_id, existing_content):
        sys.stderr.write("###Rcv'd PUT \n'" );
        """Append data into channel."""
        ttl, ids, old_data, old_etag = existing_content

        data = request.body
        sys.stderr.write("   body len: %s \n" % len(data));
        etag = self._etag(data)

        # check the If-Match header
        if 'If-Match' in request.headers:
            if str(request.if_match) != '*':
                # if If-Match is provided, it must be the value of
                # the etag before the update is applied
                if not self._etag_match(old_etag, request.if_match):
                    raise HTTPPreconditionFailed(etag=etag)
        elif 'If-None-Match' in request.headers:
            if str(request.if_none_match) == '*':
                # we will put data in the channel only if it's
                # empty (== first PUT)
                if old_data != _EMPTY:
                    raise HTTPPreconditionFailed(etag=etag,
                            headers=copy.deepcopy(self.CORS_HEADERS))

        if not self.cache.set(channel_id, (ttl, ids, request.body, etag),
                              time=ttl):
            raise HTTPServiceUnavailable(headers=
                    copy.deepcopy(self.CORS_HEADERS))

        sys.stderr.write("### Return success \n");

        return json_response('', etag=etag, 
                headers=copy.deepcopy(self.CORS_HEADERS))

    def get_channel(self, request, channel_id, existing_content):
        """Grabs data from channel if available."""
        ttl, ids, data, etag = existing_content

        # check the If-None-Match header
        if request.if_none_match is not None:
            if self._etag_match(etag, request.if_none_match):
                raise HTTPNotModified(headers=
                        copy.deepcopy(self.CORS_HEADERS))

        # keep the GET counter up-to-date
        # the counter is a separate key
        deletion = False
        ckey = 'GET:%s' % channel_id
        count = self.cache.get(ckey)
        if count is None:
            self.cache.set(ckey, '1')
        else:
            if int(count) + 1 == self.max_gets:
                # we reached the last authorized call, the channel is remove
                # after that
                deletion = True
            else:
                self.cache.incr(ckey)

        try:
            import pdb; pdb.set_trace();
            sys.stderr.write('dumping data: ' + json.dumps(data) + "\n")
            return json_response(data, dump=False, etag=etag, 
                    headers=copy.deepcopy(self.CORS_HEADERS))
        finally:
            # deleting the channel in case we did all GETs
            if deletion:
                if not self._delete_channel(channel_id):
                    log_cef('Could not delete the channel', 5,
                            request.environ, self.config,
                            msg=_cid2str(channel_id))

    def _delete_channel(self, channel_id):
        self.cache.delete('GET:%s' % channel_id)
        res = self.cache.get(channel_id)
        if res is None:
            return True   # already gone
        return self.cache.delete(channel_id)

    def blacklisted(self, ip, environ):
        log_cef('BlackListed IP', 5, environ, self.config, msg=ip)

    def report(self, request, client_id):
        """Reports a log and delete the channel if relevant"""
        # logging the report
        log = []
        header_log = request.headers.get('X-KeyExchange-Log')
        if header_log is not None:
            log.append(header_log)

        body_log = request.body[:2000].strip()
        if body_log != '':
            log.append(body_log)

        # logging only if the log is not empty
        if len(log) > 0:
            log = '\n'.join(log)
            log_cef('Report', 5, request.environ, self.config, msg=log)

        # removing the channel if present
        channel_id = request.headers.get('X-KeyExchange-Cid')
        if client_id is not None and channel_id is not None:
            content = self.cache.get(channel_id)
            if content is not None:
                # the channel is still existing
                ttl, ids, data, etag = content

                # if the client_ids is in ids, we allow the deletion
                # of the channel
                if not self._delete_channel(channel_id):
                    log_cef('Could not delete the channel', 5,
                            request.environ, self.config,
                            msg=_cid2str(channel_id))
        return json_response('', 
                headers=copy.deepcopy(self.CORS_HEADERS))


def make_app(global_conf, **app_conf):
    """Returns a Key Exchange Application."""
    global_conf.update(app_conf)
    config = Config(global_conf)
    app = KeyExchangeApp(config)
    blacklisted = app.blacklisted

    # hooking a profiler
    if global_conf.get('profile', 'false').lower() == 'true':
        from repoze.profile.profiler import AccumulatingProfileMiddleware
        app = AccumulatingProfileMiddleware(app, log_filename='profile.log',
                                          cachegrind_filename='cachegrind.out',
                                          discard_first_request=True,
                                          flush_at_shutdown=True,
                                           path='/__profile__')

    # hooking a client debugger
    if global_conf.get('client_debug', 'false').lower() == 'true':
        from paste.exceptions.errormiddleware import ErrorMiddleware
        app = ErrorMiddleware(app, debug=True,
                              show_exceptions_in_wsgi_errors=True)

    # hooking a stdout logger
    if global_conf.get('debug', 'false').lower() == 'true':
        from paste.translogger import TransLogger
        app = TransLogger(app, logger_name='jpakeapp',
                          setup_console_handler=True)

    # IP Filtering middleware
    if config.get('filtering.use', False):
        del config['filtering.use']
        params = config.get_section('filtering')
        app = IPFiltering(app, callback=blacklisted, **params)

    return app
