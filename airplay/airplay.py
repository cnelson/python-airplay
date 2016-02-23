import atexit
import plistlib
import socket
import time
import urllib

from BaseHTTPServer import BaseHTTPRequestHandler
from httplib import HTTPResponse
from mimetools import Message
from multiprocessing import Process, Queue
from Queue import Empty
from StringIO import StringIO

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf


class FakeSocket():
    """Use StringIO to pretend to be a socket like object that supports makefile()"""
    def __init__(self, data):
        self._str = StringIO(data)

    def makefile(self, *args, **kwargs):
        """Returns the StringIO object.  Ignores all arguments"""
        return self._str


class AirPlayEvent(BaseHTTPRequestHandler):
    """Parse an AirPlay event delivered over Reverse HTTP"""

    def do_GET(self):
        raise NotImplementedError

    def do_HEAD(self):
        raise NotImplementedError

    def do_POST(self):
        """Called when a new event has been received"""

        # make sure this is what we expect
        if self.path != '/event':
            raise RuntimeError('Unexpected path when parsing event: {0}'.format(self.path))

        # validate our content type
        content_type = self.headers.get('content-type', None)
        if content_type != 'text/x-apple-plist+xml':
            raise RuntimeError('Unexpected Content-Type when parsing event: {0}'.format(content_type))

        # and the body length
        content_length = int(self.headers.get('content-length', 0))
        if content_length == 0:
            raise RuntimeError('Received an event with a zero length body.')

        # parse XML plist
        self.event = plistlib.readPlistFromString(self.rfile.read(content_length))


class AirPlay(object):
    """Locate and control devices supporting the AirPlay server protocol for video
    This implementation is based on section 4 of https://nto.github.io/AirPlay.html

    For detailed information on most methods and responses, please see the specification.

    """
    RECV_SIZE = 8192

    def __init__(self, host, port=7000, name=None, timeout=5):
        """Connect to an AirPlay device on `host`:`port` optionally named `name`

        Args:
            host(string):   Hostname or IP address of the device to connect to
            port(int):      Port to use when connectiong
            name(string):   Optional. The name of the device.
            timeout(int):   Optional. A timeout for socket operations

        Raises:
            ValueError:     Unable to connect to the specified host/port
        """

        self.host = host
        self.port = port
        self.name = name

        # connect the control socket
        try:
            self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.control_socket.settimeout(timeout)
            self.control_socket.connect((host, port))
        except socket.error as exc:
            raise ValueError("Unable to connect to {0}:{1}: {2}".format(host, port, exc))

    def _monitor_events(self, event_queue, control_queue):  # pragma: no cover
        """Connect to `host`:`port` and use reverse HTTP to receive events.

        This function will block until any message is received via `control_queue`
        Which a message is received via that queue, the event socket is closed, and this
        method will return.


        Args:
            event_queue(Queue):     A queue which events will be put into as they are received
            control_queue(Queue):   If any messages are received on this queue, this function will exit

        Raises:
            Any exceptions raised by this method are caught and sent through
            the `event_queue` and handled in the main process
        """

        try:
            # connect to the host
            event_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            event_socket.connect((self.host, self.port))

            # "upgrade" this connection to Reverse HTTP
            raw_request = "POST /reverse HTTP/1.1\r\nUpgrade: PTTH/1.0\r\nConnection: Upgrade\r\n\r\n"
            event_socket.send(raw_request)

            raw_response = event_socket.recv(AirPlay.RECV_SIZE)
            resp = HTTPResponse(FakeSocket(raw_response))
            resp.begin()

            # if it was successfully, we should get code 101 'switching protocols'
            if resp.status != 101:
                raise RuntimeError(
                    "Unexpected response from AirPlay when setting up event listener.\n"
                    "Expected: HTTP/1.1 101 Switching Protocols\n\n"
                    "Sent:\n{0}Received:\n{1}".format(raw_request, raw_response)
                )

            # now we loop forever, receiving events as HTTP POSTs to us
            event_socket.settimeout(.1)

            while True:
                # see if the parent asked us to exit
                try:
                    control_queue.get(block=False)
                    event_socket.close()
                    return
                except Empty:
                    pass

                # receive a request
                try:
                    raw_request = event_socket.recv(AirPlay.RECV_SIZE)
                except socket.timeout:
                    continue

                # parse it
                try:
                    req = AirPlayEvent(FakeSocket(raw_request), event_socket.getpeername(), None)
                except RuntimeError as exc:
                    raise RuntimeError(
                        "Unexpected request from AirPlay while processing events\n"
                        "Error: {0}\nReceived:\n{1}".format(exc, raw_request)
                    )

                # acknowledge it
                event_socket.send("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

                # skip non-video events
                if req.event.get('category', None) != 'video':
                    continue

                # send the event back to the parent process
                event_queue.put(req.event)

        except KeyboardInterrupt:
            return
        except Exception as exc:
            event_queue.put(exc)
            return

    def events(self, block=True):
        """A generator that produces a list of events from the AirPlay Server

        Args:
            block(bool):    If true, this function will block until an event is available
                            If false, the generator will stop when there are no more events

        Yields:
            dict:           An event provided by the AirPlay server

        """
        # set up our event socket reader in another process if we haven't
        # already done so.
        try:
            getattr(self, 'event_queue')
        except AttributeError:
            # TODO: switch to Pipe?
            self.event_queue = Queue()
            self.event_control = Queue()

            self.event_monitor = Process(target=self._monitor_events, args=[self.event_queue, self.event_control])
            self.event_monitor.start()

            # ensure when we shutdown, that the child proess does as well
            # this needs to be called _after_ the call to Process.start()
            # as multiprocessing also registers atexit handlers, and we want
            # ours to run first, Since atexit is LIFO we go last to get run first
            atexit.register(lambda: self.event_control.put(True))

        # loop forever processing events sent to us by the child process
        while True:
            try:
                event = self.event_queue.get(block=block, timeout=None)
                # if we were sent an exception, then something went wrong
                # in the child process, so reraise it here
                if isinstance(event, Exception):
                    raise event

                # otherwise, it's just an event
                yield event
            except Empty:
                return

    def _command(self, uri, method='GET', body='', **kwargs):
        """Makes an HTTP request through to an AirPlay server

        Args:
            uri(string):    The URI to request
            method(string): The HTTP verb to use when requesting `uri`, defaults to GET
            body(string):   If provided, will be sent witout alteration as the request body.
                            Content-Length header will be set to len(`body`)
            **kwargs:       If provided, Will be converted to a query string and appended to `uri`

        Returns:
            True: Request returned 200 OK, with no response body
            False: Request returned something other than 200 OK, with no response body

            Mixed: The body of the HTTP response
        """

        # generate the request
        if len(kwargs):
            uri = uri + '?' + urllib.urlencode(kwargs)

        request = "{0} {1} HTTP/1.1\r\nContent-Length: {2}\r\n\r\n{3}".format(method, uri, len(body), body)

        # send it
        self.control_socket.send(request)

        # parse our response
        result = self.control_socket.recv(self.RECV_SIZE)
        resp = HTTPResponse(FakeSocket(result))
        resp.begin()

        # if our content length is zero, then return bool based on result code
        if int(resp.getheader('content-length', 0)) == 0:
            if resp.status == 200:
                return True
            else:
                return False

        # else, parse based on provided content-type
        # and return the response body
        content_type = resp.getheader('content-type')

        if content_type is None:
            raise RuntimeError('Response returned without a content type!')

        if content_type == 'text/parameters':
            return Message(StringIO(resp.read()))

        if content_type == 'text/x-apple-plist+xml':
            return plistlib.readPlistFromString(resp.read())

        raise RuntimeError('Response received with unknown content-type: {0}'.format(content_type))

    def get_property(self, *args, **kwargs):
        """What it says on the tin"""
        raise NotImplementedError('Methods that require binary plists are not supported.')

    def set_property(self, *args, **kwargs):
        """What it says on the tin"""
        raise NotImplementedError('Methods that require binary plists are not supported.')

    def server_info(self):
        """Fetch general informations about the AirPlay server.

        Returns:
            dict: key/value pairs that describe the server.
        """
        return self._command('/server-info')

    def play(self, url, pos=0.0):
        """Start video playback.

        Args:
            url(string):    A URL to video content that the AirPlay server is capable of playing
            pos(float):     The position in the content to being playback. 0.0 = start, 1.0 = end.

        Returns:
            bool: The request was accepted.

        Note: A result of True does not mean that playback will succeed, simply
        that the AirPlay server accepted the request and will *attempt* playback
        """

        return self._command('/play', 'POST', "Content-Location: {0}\nStart-Position: {1}\n\n".format(url, float(pos)))

    def rate(self, rate):
        """Change the playback rate.

        Args:
            rate(float) The playback rate: 0.0 is paused, 1.0 is playing at the normal speed.

        Returns:
            True: The playback rate was changed
            False: The playback rate requested was invalid
        """
        return self._command('/rate', 'POST', value=float(rate))

    def stop(self):
        """Stop playback.

        Note: This does not seem to generate a 'stopped' event from the AirPlay server when called

        Returns:
            True: Playback was stopped.
        """
        return self._command('/stop', 'POST')

    def playback_info(self):
        """Retrieve playback informations such as position, duration, rate, buffering status and more.

        Returns:
            dict: key/value pairs describing the playback state
            False: Nothing is currently being played
        """

        return self._command('/playback-info')

    def scrub(self, position=None):
        """Return the current position or seek to a specific position

        If `position` is not provided returns the current position.  If it is
        provided, seek to that position and return it.

        Args:
            position(float):    The position to seek to.  0.0 = start 1.0 = end"

        Returns:
            dict:   A dict like: {'duration': float(seconds), 'position': float(seconds)}

        """
        args = {}
        method = 'GET'

        if position:
            method = 'POST'
            args['position'] = position

        response = self._command('/scrub', method, **args)

        # convert the strings we get back to floats (which they should be)
        return {kk: float(vv) for (kk, vv) in response.items()}

    @classmethod
    def find(cls, timeout=10, fast=False):
        """Use Zeroconf/Bonjour to locate AirPlay servers on the local network

        Args:
            timeout(int):   The number of seconds to wait for responses.
                            If fast is false, then this function will always block for this number of seconds.
            fast(bool):     If true, do not wait for timeout to expire,
                            return as soon as we've found at least one AirPlay server

        Returns:
            list:   A list of AirPlay() objects; one for each AirPlay server found

        """

        # this will be our list of devices
        devices = []

        # zeroconf will call this method when a device is found
        def on_service_state_change(zeroconf, service_type, name, state_change):
            if state_change is ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info is None:
                    return

                try:
                    name, _ = name.split('.', 1)
                except ValueError:
                    pass

                devices.append(
                    cls(socket.inet_ntoa(info.address), info.port, name)
                )

        # search for AirPlay devices
        zeroconf = Zeroconf()

        browser = ServiceBrowser(zeroconf, "_airplay._tcp.local.", handlers=[on_service_state_change])  # NOQA

        # enforce the timeout
        timeout = time.time() + timeout
        try:
            while time.time() < timeout:
                # if they asked us to be quick, bounce as soon as we have one AirPlay
                if fast and len(devices):
                    break
                time.sleep(0.05)
        except KeyboardInterrupt:  # pragma: no cover
            pass
        finally:
            zeroconf.close()

        return devices
