# django-channels-router

A Python library for routing and handling websocket messages using django-channels.

## Routing socket messages in the view
(a shorthand for the Handlers method below)

**views.py**
```python
from django.views import View
from django.shortcuts import render
from sockets import SocketRouter

class MyPage(View):
    socket = SocketRouter()

    def get(self, request):
        return render('my_page.html')

    def post(self, request):
        ...

    @socket.connect
    def on_open(socket):
        socket.send_json({'connected': True})

    @socket.route('PING')
    def on_ping(socket, content):
        socket.send_json({
            'type': 'PONG',
            'ts': datetime.now().timestamp(),
        })

    @socket.route(re.compile('GAME_ACTION_.+'))
    def on_game_action(socket, content):
        socket.send_json({
            'received_action': content['action'],
            'action_type': content['type'],
        })

    @socket.default_route
    def default_route(socket, content=None):
        socket.send_json({
            'type': 'ERROR',
            'details': 'Got unknown message: {}'.format(content),
        })

    @socket.disconnect
    def on_disconnect(socket, message):
        Socket.objects.all().send_json({
            'type': 'CHAT',
            'recvd_message': 'User {} left chat.'.format(socket.user.username),
        })
```

**urls.py**
```python
from channels.routing import route_class
from .views import MyPage

urlpatterns = [
    ...
    url(r'^mypage/$', MyPage.as_view(), name='Table'),
]

socket_routing = [
    ...
    route_class(MyPage.socket.Handler, path=r'^/mypage/$'),
]
```

## Routing socket messages using a handler

Essentially a class-based view for websocket messages, where the handler function is determined by routing on the message['type'] key.

**handlers.py**
```python
from sockets.handlers import RoutedSocketHandler

class PokerSocketHandler(RoutedSocketHandler):
    login_required = False

    routes = (
        *RoutedSocketHandler.routes,
        ('GET_GAMESTATE', 'on_get_gamestate'),
        (re.compile('ACT_.+'), 'on_game_action'),
    )

    def on_get_gamestate(self, content):
        ....

    def on_game_action(self, content):
        ....
```

**views.py**
```python
from django.views import View
from django.shortcuts import render
from sockets import SocketRouter
from .handlers import PokerSocketHandler

class MyPage(View):
    socket = SocketRouter(handler=PokerSocketHandler)

    def get(self, request):
        return render('gmae.html')
```

### Sessions

This is the spec for the django-channels-router Socket object which links a user's sockets to their HTTP Session.

```python
class Socket(BaseModel):
    """Represents a single websocket connection to a user."""

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
```
