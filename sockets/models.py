import datetime
import json
import logging

from hashlib import md5
from channels import Channel, Group

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib import admin
from django.contrib.gis.geoip2 import GeoIP2
from django.contrib.sessions.models import Session

from grater.utils import ExtendedEncoder, log_io_message
from grater.model_utils import BaseModel


logger = logging.getLogger('sockets')

from .constants import (
    HELLO_TYPE,
    GOT_HELLO_TYPE,
    PING_TYPE,
    PING_RESPONSE_TYPE,
    RECONNECT_TYPE,
    ROUTING_KEY,
)


class SocketQuerySet(models.QuerySet):
    """
    represents a group of socket connections, to mass-send use
    .send_action() and .send_json()
    """
    _channel_names = ()

    def group(self):
        """
        get a django channels Group consisting of all the
        reply_channels in the QuerySet
        """
        self._channel_names = self.values_list('channel_name', flat=True)

        if not self._channel_names:
            empty_group = Group('emptyname')
            empty_group.empty = True
            return empty_group

        # group name is the hash of all the channel_names
        combined_names = b''.join(
            i.encode('utf-8', errors='replace')
            for i in self._channel_names
        )
        group_id = md5(combined_names).hexdigest()
        combined_group = Group(name=group_id)

        for channel_name in self._channel_names:
            if channel_name.startswith('bot-'):
                continue
            combined_group.add(Channel(channel_name))
        return combined_group

    def send_str(self, content: str):
        group = self.group()
        # Group.send and group.empty check is necessary to prevent
        #   ChannelFull errors caused by sending to empty group or
        #   group with dead Channels. do not replace this with a for
        #   loop that sends to each channel separately
        if not getattr(group, 'empty', False):
            group.send({'text': content})
            return len(self._channel_names)
        return 0

    def send_json(self, content: dict):
        """send some json to the entire QuerySet of Sockets"""
        now_str = str(datetime.datetime.timestamp(timezone.now()) * 1000)
        content.update({'TIMESTAMP': now_str})
        log_io_message(out=True, content=content)
        encoded_json = json.dumps(content, cls=ExtendedEncoder)
        return self.send_str(encoded_json)

    def send_action(self, action_type: str, **kwargs):
        """send an action to the entire QuerySet of Sockets"""
        return self.send_json({**kwargs, ROUTING_KEY: action_type})

    def mark_active(self):
        return self.update(active=True)

    def mark_inactive(self):
        return self.update(active=False)

    def cleanup_stale(self):
        """
        Ask all the active Sockets in the queryset to ping us back,
        to confirm they're still open
        """
        # print(f'Asking {self.count()} potentially stale sockets for '\
        #        'a PING back.')
        self.filter(active=True).update(active=False)
        # ask the frontend to PING us back to confirm they're still active
        self.send_action(PING_TYPE)
        self.purge_inactive()

    def purge_inactive(self, in_last_mins=5):
        """delete all the sockets with a last_ping over 1 hour ago"""
        n_mins_ago = timezone.now() - datetime.timedelta(minutes=in_last_mins)

        num_deleted, _ = self.filter(
            active=False,
            last_ping__lt=n_mins_ago
        ).delete()
        # if num_deleted and settings.DEBUG:
        #     print(f'Purged {num_deleted} stale sockets.')
        return num_deleted


class Socket(BaseModel):
    """Represents a single websocket connection to a user."""

    # custom manager allows performing some actions on groups of
    #   Socket connections by using methods on the queryset defined
    #   above
    objects = SocketQuerySet.as_manager()

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )
    channel_name = models.CharField(max_length=64, unique=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.CASCADE
    )
    path = models.CharField(max_length=128, db_index=True)

    active = models.BooleanField(default=False)
    last_ping = models.DateTimeField(null=True)
    user_ip = models.CharField(max_length=15, null=True, blank=True)

    def __repr__(self):
        """<Socket username@/table/1234 (inactive)>"""
        uname = self.user.username if self.user else 'anon'
        is_inactive = '' if self.active else ' (inactive)'
        return f'<Socket {uname}@{self.path}{is_inactive}>'

    def geoip(self):
        if not self.user_ip or self.user_ip == '127.0.0.1':
            return None

        try:
            g = GeoIP2()
            return g.city(self.user_ip)
        except Exception:
            return None

    @property
    def usersession(self):
        return self.session.usersession

    @property
    def reply_channel(self):
        return Channel(self.channel_name)

    def send_str(self, content: str):
        if not self.channel_name.startswith('bot-'):
            self.reply_channel.send({'text': content})

    def send_json(self, content: dict):
        now_str = str(datetime.datetime.timestamp(timezone.now()) * 1000)
        content.update({'TIMESTAMP': now_str})
        self.log_message(out=True, content=content)
        encoded_json = json.dumps(content, cls=ExtendedEncoder)
        self.reply_channel.send({'text': encoded_json})

    def send_action(self, action_type: str, **kwargs):
        self.send_json({**kwargs, ROUTING_KEY: action_type})

    def cleanup_stale(self):
        """
        check for other connections on the same path, and set them
        to inactive if no longer connected
        """

        if not self.user:
            # should not try to find related sockets for anonymous
            #   users, it will spam all anon sockets on the same page
            #   with a PING, and thrash us with their simultaneous
            #   responses
            return

        # optionally, remove the path=self.path filter to check ALL
        #   the user's active sockets
        related = Socket.objects\
                        .filter(user=self.user, path=self.path)\
                        .exclude(id=self.id)
        related.cleanup_stale()

    def log_message(self, out=True, content=None, unknown=False):
        """log pretty websocket messages to console for easy flow debugging"""
        log_io_message(out=out, content=content, unknown=unknown)
