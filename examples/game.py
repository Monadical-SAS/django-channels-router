from django.views import View
from django.shortcuts import render

from socket_router import RoutedSocketHandler, SocketRouter


class MyPage(View):
    socket = SocketRouter(handler=RoutedSocketHandler)

    def get(self, request):
        return render('my_page.html')

    @socket.connect
    def on_open(self):
        # self inside of decorated functions refers to MyPage.socket.Handler(), not MyPage()
        # (the socket's view class is separate from the page's view class)
        # the socket.Handler class can be extended by inheriting from RoutedSocketHandler above
        self.send_json({'connected': True})

    @socket.default_route
    def default_route(self, content=None):
        self.send_json({
            'type': 'ERROR',
            'details': 'Got unknown message: {}'.format(content),
        })

    @socket.route('UPDATE_USER')
    def on_update_user(self, content=None):
        ...

    @socket.route(re.compile('HELLO.*'))
    def on_hello(self, content):
        self.send_json({'received_hello': data, 'on_channel_name': self.channel_name})

    @socket.disconnect
    def on_disconnect(self, message):
        self.broadcast_action('CHAT', recvd_message="spectator disconnected")
