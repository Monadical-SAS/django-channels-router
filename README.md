# django-channels-router

A Python library for routing and handling websocket messages using django-channels.

## Example Usage

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
