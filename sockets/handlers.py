import traceback
import logging

from django.conf import settings
from django.utils import timezone

from grater.utils import log_io_message, ANSI

from channels.generic.websockets import JsonWebsocketConsumer

from .models import Socket
from .constants import (
    PING_RESPONSE_TYPE,
    HELLO_TYPE,
    GOT_HELLO_TYPE,
    RECONNECT_TYPE,
    ROUTING_KEY,
)


logger = logging.getLogger('sockets')


class RoutedSocketHandler(JsonWebsocketConsumer):
    """All the methods and attributes available to every websocket request"""

    ### Properties used to set up JsonWebsocketConsumer behavior

    # by default, dont require user to be logged in to get messages
    login_required = False
    # guess the user from the http connection header, and attach
    #   it to all messages
    http_user = True
    socket = None

    # add shared handlers you want every websocket to have here:
    routes = (
        # the first parameter is either a string for exact matching
        #   against the incoming type key, or a compiled regex
        #   for regex matching
        # the second parameter is either a function, or the string
        #   name of a function on self
        # e.g. ('GET_TABLE', 'on_get_table')
        (PING_RESPONSE_TYPE, 'on_ping'),
        (HELLO_TYPE, 'on_hello'),
    )

    def setup_session(self, extra=None):
        # initialize the socket DB model which stores connection state
        #   and owns the handler functions
        extra = extra or {}
        if self.message.http_session:
            extra['session_id'] = self.message.http_session.session_key

        self.socket, created = Socket.objects.update_or_create(
            channel_name=self.reply_channel.name,
            defaults={
                'user': self.user,
                'path': self.path,
                'active': True,
                'last_ping': timezone.now(),
                **extra,
            },
        )

    @property
    def reply_channel(self):
        return self.message.reply_channel

    @property
    def view_name(self):
        return self.__class__.__name__

    @property
    def user(self):
        if not self.message.user.is_authenticated:
            return None
        return self.message.user

    def connect(self, message=None, initialize=True):
        ip_header = [
            header[1] for header in message['headers']
            if header[0] == b'x-real-ip'
        ]
        user_ip = ip_header[0] if ip_header else message['client'][0]

        self.setup_session({'user_ip': user_ip})
        if initialize:
            # otherwise HANDSHAKE is never finished
            self.socket.reply_channel.send({'accept': True})

    def disconnect(self, message, **kwargs):
        """called when the socket disconnects"""
        self.setup_session()
        user_ip = self.socket.user_ip
        last_ping = self.socket.last_ping
        self.socket.delete()

        if message.content['code'] == 1006:
            details = {
                'code': message.content['code'],
                'path': message.content['path'],
                'method': message.content['method'],
                'order': message.content['order'],
                'reply_channel': message.content['reply_channel'],
                'last_ping': last_ping,
                'user_id': self.user.id if self.user else None,
                'user_ip': user_ip,
            }
            print(f'{ANSI["red"]}[!] Closed websocket due to overloaded '\
                  f'server! {ANSI["reset"]}')
            if settings.DEBUG:
                print('\n'.join(f'{k}: {v}' for k, v in details.items()))
            else:
                # dropping sockets for logged-in users is very bad, for
                #   spectators it's less of an issue
                if self.user:
                    logger.exception('Closed websocket due to overloaded '\
                                     'server.', extra=details)
                else:
                    logger.debug('Closed websocket due to overloaded '\
                                 'server.', extra=details)

    def send_action(self, action_type: str, **kwargs):
        self.setup_session()
        self.socket.send_action(action_type=action_type, **kwargs)

    def receive(self, content: dict, **kwargs):
        """pass parsed json message to appropriate handler in self.routes"""

        if self.login_required and not self.user:
            # User needs to reconnect to access sesion data (happens
            #   when redis sessions table is erased)
            self.send_action(
                RECONNECT_TYPE,
                details='No session was attached to socket, '\
                        'the frontend should try reconnecting.'
            )
            return None

        assert isinstance(content, dict), \
                f'Expected JSON websocket message to be a dictionary, '\
                f'but got {type(content).__name__}'
        action_type = content.get(ROUTING_KEY)

        if not action_type:
            # if it's missing the 'type' attr, give it to default handler
            return self.default_route(content)

        # run through route patterns looking for a match to handle the msg
        for pattern, handler in reversed(self.routes):
            # if pattern is a compiled regex, try matching it
            if hasattr(pattern, 'match') and pattern.match(action_type):
                break
            # if pattern is just a str, check for exact match
            if action_type == pattern:
                break
        else:
            # if no route matches, fall back to default handler
            handler = None

        try:
            if handler:
                self.log_message(out=False, content=content, unknown=False)

                if hasattr(handler, '__call__'):
                    # if handler is a function already, call it
                    handler(self, content)
                else:
                    # otherwise fetch the function on self with the
                    #   handler's name
                    handler_func = getattr(self, handler, None)
                    assert handler_func, \
                            f'No handler function with name {handler} exists'
                    handler_func(content)
            else:
                self.default_route(content)
        except Exception as e:
            self.setup_session()
            if settings.DEBUG:
                # if backend exception occurs, send over websocket for display
                stacktrace = traceback.format_exc()
                self.send_action(
                    'ERROR',
                    success=False,
                    errors=[repr(e)],
                    details=stacktrace,
                )
                print(stacktrace)
            logger.exception(e, extra={'user': self.user.attrs((
                'id', 'username', 'name', 'email', 'first_name', 'last_name',
                'date_joined', 'is_staff', 'is_active'))})

    def default_route(self, content):
        """default handler for messages that dont match any route patterns"""
        self.log_message(out=False, content=content, unknown=True)
        self.send_action(
            'ERROR',
            details=f'Unknown action: {content.get(ROUTING_KEY)}'
        )
        logger.error(f'Unrecognized websocket msg: {content}')

    def on_hello(self, content):
        """
        respond to websocket initial HELLO, confirms connection
        and round-trip-time
        """
        self.setup_session()
        self.send_action(
            GOT_HELLO_TYPE,
            user_id=self.user.id if self.user else None,
            session_id=self.socket.id,
            path=self.path,
            last_ping=self.socket.last_ping,
            user_ip=self.socket.user_ip,
            # geoip=self.socket.geoip(),
        )

        # when the user refreshes/loads a page
        # confirm any other sockets they own on the same page are active
        self.socket.cleanup_stale()

    def on_ping(self, content):
        """
        when the frontend responds to a ping, confirm that their
        socket is still alive
        """
        self.setup_session()

    def log_message(self, out=True, content=None, unknown=False):
        """
        log pretty websocket messages to console for easy flow
        debugging
        """
        log_io_message(out=out, content=content, unknown=unknown)
