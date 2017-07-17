from django.test import TestCase

from .router import SocketRouter



class RouterTest(TestCase):
    def setUp(self):
        self.router = SocketRouter()



# TODO: write tests for socket handler and socket routing
# TODO: write tests for model creation on connection
# TODO: write tests for setting sockets inactive when no ping response
