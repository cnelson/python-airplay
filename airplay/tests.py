import email
import os
import shutil
import socket
import tempfile
import time
import unittest
import warnings

try:
    from urllib2 import Request
    from urllib2 import urlopen
    from urllib2 import URLError
except ImportError:
    from urllib.request import Request
    from urllib.request import urlopen
    from urllib.error import URLError

try:
    from mock import call, patch, Mock
except ImportError:
    from unittest.mock import call, patch, Mock

from zeroconf import ServiceStateChange

from .airplay import FakeSocket, AirPlayEvent, AirPlay, RangeHTTPServer

from .ffmpeg import FFmpeg, EncoderNotInstalledError, MediaParseError


class TestFakeSocket(unittest.TestCase):
    def test_socket(self):
        """When using the FakeSocket we get the same data out that we put in"""

        f = FakeSocket(b"foo")

        assert f.makefile().read() == b"foo"


class TestAirPlayEvent(unittest.TestCase):

    # TODO: Move these fixtures to external files
    GET_REQUEST = b"GET /event HTTP/1.1\r\nConnection: close\r\n\r\n"
    HEAD_REQUEST = b"HEAD /event HTTP/1.1\r\nConnection: close\r\n\r\n"

    BAD_PATH_REQUEST = b"POST /foo HTTP/1.1\r\nConnection: close\r\n\r\n"

    NO_CONTENT_TYPE_REQUEST = b"POST /event HTTP/1.1\r\nConnection: close\r\n\r\n"
    BAD_CONTENT_TYPE_REQUEST = b"POST /event HTTP/1.1\r\nConnection: close\r\nContent-Type: foo\r\n\r\n"

    NO_CONTENT_LENGTH_REQUEST = b"POST /event HTTP/1.1\r\nConnection: close\r\nContent-Type: text/x-apple-plist+xml\r\n\r\n"  # NOQA
    BAD_CONTENT_LENGTH_REQUEST = b"POST /event HTTP/1.1\r\nConnection: close\r\nContent-Type: text/x-apple-plist+xml\r\nContent-Length: 0\r\n\r\n"  # NOQA
    GOOD_REQUEST = b"""POST /event HTTP/1.1\r\nConnection: close\r\nContent-Type: text/x-apple-plist+xml\r\nContent-Length: 227\r\n\r\n<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n\t<key>test</key>\n\t<string>foo</string>\n</dict>\n</plist>"""  # NOQA

    def parse_request(self, req):
        return AirPlayEvent(FakeSocket(req), ('192.0.2.23', 916), None)

    def test_bad_methods(self):
        """Only POST requests are supported"""

        self.assertRaises(NotImplementedError, self.parse_request, self.GET_REQUEST)
        self.assertRaises(NotImplementedError, self.parse_request, self.HEAD_REQUEST)

    def test_bad_path(self):
        """Requests not made to /event raise RuntimeError"""

        self.assertRaises(RuntimeError, self.parse_request, self.BAD_PATH_REQUEST)

    def test_bad_content_type(self):
        """Requests with invalid content-types raise RuntimeError"""

        self.assertRaises(RuntimeError, self.parse_request, self.NO_CONTENT_TYPE_REQUEST)
        self.assertRaises(RuntimeError, self.parse_request, self.BAD_CONTENT_TYPE_REQUEST)

    def test_bad_content_length(self):
        """Requests with invalid content-length raise RuntimeError"""

        self.assertRaises(RuntimeError, self.parse_request, self.NO_CONTENT_LENGTH_REQUEST)
        self.assertRaises(RuntimeError, self.parse_request, self.BAD_CONTENT_LENGTH_REQUEST)

    def test_good_request(self):
        """Requests with valid plists are parsed correctly"""

        # parse our simple request wihich has a plist that defines 'test' == 'foo'
        req = self.parse_request(self.GOOD_REQUEST)

        # make sure we parsed it correctly
        assert req.event['test'] == 'foo'


class TestAirPlayEventMonitor(unittest.TestCase):
    # mock socket to return our test object
    # send => drop
    # recv => return fixture response 101, then whatever

    # insepect queues for results

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def setUp(self, mock):

        mock.sock = MockSocket()
        mock.sock.recv_data = """HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n"""

        self.ap = AirPlay('192.0.2.23', 916, 'test')

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_event_bad_upgrade(self, mock):
        """When 101 response is not returned on the event socket, RuntimerError is raised"""

        def go():
            list(self.ap.events(block=True))

        self.assertRaises(RuntimeError, go)

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_event_socket_closed_control(self, mock):
        """When a message is received on the control queue, the socket is closed"""

        # start the event listener
        list(self.ap.events(block=False))

        # ensure it's running
        assert self.ap.event_monitor.is_alive()

        # tell it to die
        self.ap.event_control.put(True)

        # wait for timeout to occur
        time.sleep(1)

        # it should be dead
        assert self.ap.event_monitor.is_alive() is False

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_bad_event(self, mock):
        """When an unparseable event is received, RuntimeError is raised"""

        mock.sock.recv_data = [
            """HTTP/1.1 101 Switching Protocols\r\nContent-Length: 0\r\n\r\n""",
            """POST /event HTTP/1.1\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nhi"""
        ]

        def go():
            list(self.ap.events(block=True))

        self.assertRaises(RuntimeError, go)

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_non_video_event(self, mock):
        """Events that are not video related are not forwarded to the queue"""

        mock.sock.recv_data = [
            """HTTP/1.1 101 Switching Protocols\r\nContent-Length: 0\r\n\r\n""",
            """POST /event HTTP/1.1\r\nContent-Type: text/x-apple-plist+xml\r\nContent-Length: 303\r\n\r\n<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>category</key><string>photo</string><key>sessionID</key><integer>13</integer><key>state</key><string>paused</string></dict></plist>"""  # NOQA
        ]

        gen = self.ap.events(block=True)

        def go():
            try:
                next(gen)
            except TypeError:
                raise socket.timeout

        # TODO: Fix this whole fucking test, it's gross
        # note: this is not the real behaivor of the code, but a by product
        # of their being no more events in our MockSocket mock
        # so this error is raised
        # the TypeError above, I _think_ is called by this:
        # http://stackoverflow.com/questions/18163697/exception-typeerror-warning-sometimes-shown-sometimes-not-when-using-throw-meth
        # but haven't debugged fully
        self.assertRaises(socket.timeout, go)

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_good_event(self, mock):
        """When we receive a properly formatted video event, we forward it to the queue"""

        mock.sock.recv_data = [
            """HTTP/1.1 101 Switching Protocols\r\nContent-Length: 0\r\n\r\n""",
            """POST /event HTTP/1.1\r\nContent-Type: text/x-apple-plist+xml\r\nContent-Length: 303\r\n\r\n<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>category</key><string>video</string><key>sessionID</key><integer>13</integer><key>state</key><string>paused</string></dict></plist>"""  # NOQA
        ]

        gen = self.ap.events(block=True)

        ev = next(gen)

        assert ev['category'] == 'video'
        assert ev['state'] == 'paused'

    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def test_event_queue_empty(self, mock):
        """The generator stops when there are no more events"""

        # no events in this list
        mock.sock.recv_data = [
            """HTTP/1.1 101 Switching Protocols\r\nContent-Length: 0\r\n\r\n"""
        ]

        gen = self.ap.events(block=False)

        def go():
            next(gen)

        self.assertRaises(StopIteration, go)


class TestAirPlayControls(unittest.TestCase):
    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def setUp(self, mock):

        mock.sock = MockSocket()
        mock.sock.recv_data = """HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n"""

        self.ap = AirPlay('192.0.2.23', 916, 'test')

        assert self.ap.name == 'test'

    @patch('airplay.airplay.socket.socket', side_effect=socket.error)
    def test_bad_hostport(self, mock):
        """ValueError is raised when socket setup/connect fails"""
        def go():
            AirPlay('192.0.2.23', 916, 'test')

        self.assertRaises(ValueError, go)

    def test_uri_only(self):
        """When called _command with just an uri, a GET request is generated"""

        self.ap._command('/foo')

        assert self.ap.control_socket.send_data.startswith(b'GET /foo')

    def test_uri_kwargs(self):
        """When _command is called with kwargs they are converted to a query string"""

        self.ap._command('/foo', bar='bork')

        assert self.ap.control_socket.send_data.startswith(b'GET /foo?bar=bork')

    def test_method(self):
        """When a method is provided, it is used in the generated request"""
        self.ap._command('/foo', method='POST')

        assert self.ap.control_socket.send_data.startswith(b'POST /foo')

    def test_body(self):
        """When a body is provided, it is included in the request with an appropriate content-length"""
        body = "lol some data"

        self.ap._command('/foo', method='POST', body=body)

        try:
            body = bytes(body, 'UTF-8')
        except TypeError:
            pass

        assert body == self.ap.control_socket.send_data[len(body) * -1:]

        assert "Content-Length: {0}".format(len(body)) in str(self.ap.control_socket.send_data)

    def test_no_body(self):
        """When no body is provided, we don't send one and content-length is 0"""
        self.ap._command('/foo', method='POST')

        assert b'Content-Length: 0' in self.ap.control_socket.send_data
        assert self.ap.control_socket.send_data.endswith(b"\r\n\r\n")

    def test_no_body_response_200(self):
        """When we receive a 200 response with no body, we return True"""

        self.ap.control_socket.recv_data = """HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"""

        assert self.ap._command('/foo') is True

    def test_no_body_response_400(self):
        """When we receive a non-200 response with no body, we return False"""

        assert self.ap._command('/foo') is False

    def test_body_no_content_type(self):
        """RutimeError is raised if we receive a body with no Content-Type"""

        self.ap.control_socket.recv_data = """HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"""

        def go():
            self.ap._command('/foo')

        self.assertRaises(RuntimeError, go)

    def test_body_content_type_param(self):
        """When Content-Type is text/parameters we return the parsed body"""

        self.ap.control_socket.recv_data = """HTTP/1.1 200 OK\r\nContent-Type: text/parameters\r\nContent-Length: 40\r\n\r\nduration: 83.124794\r\nposition: 14.467000"""  # NOQA

        res = self.ap._command('/foo')

        # note: this data is returned as strings, the scrub()
        # method will convert to float, not _command
        assert res['duration'] == '83.124794'
        assert res['position'] == '14.467000'

    def test_body_content_type_plist(self):
        """When Content-Type is text/parameters we return the parsed body"""

        self.ap.control_socket.recv_data = """HTTP/1.1 200 OK\r\nContent-Type: text/x-apple-plist+xml\r\nContent-Length: 219\r\n\r\n<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>duration</key> <real>1801</real></dict></plist>"""  # NOQA

        res = self.ap._command('/foo')

        # note: this is a plist so the data is converted by plistlib immediatelyr
        # unlike the text/parameters version above
        assert res['duration'] == 1801.0

    def test_body_content_type_unknown(self):
        """RuntimeError is raised if we receive an unknown content-type"""

        self.ap.control_socket.recv_data = """HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nhi"""   # NOQA

        def go():
            self.ap._command('/foo')

        self.assertRaises(RuntimeError, go)

    # these just all stubout _command and ensure it was called with the correct
    # parameters
    def test_get_property(self):
        """Get Property isn't implemented"""
        def go():
            self.ap.get_property()

        self.assertRaises(NotImplementedError, go)

    def test_set_property(self):
        """Set Property isn't implemented"""
        def go():
            self.ap.set_property()

        self.assertRaises(NotImplementedError, go)

    def test_server_info(self):
        """When server_info is called we pass the appropriate params"""
        self.ap._command = Mock()

        self.ap.server_info()

        self.ap._command.assert_called_with('/server-info')

    def test_play_no_pos(self):
        """When play with no position is called, we start at 0"""
        self.ap._command = Mock()

        expected_body = "Content-Location: foo\nStart-Position: 0.0\n\n"

        self.ap.play('foo')

        self.ap._command.assert_called_with('/play', 'POST', expected_body)

    def test_play_pos(self):
        """When play with position is called, we send it along"""
        self.ap._command = Mock()

        expected_body = "Content-Location: foo\nStart-Position: 0.5\n\n"

        self.ap.play('foo', position=0.5)

        self.ap._command.assert_called_with('/play', 'POST', expected_body)

    def test_rate(self):
        """When rate is called it's sent as a query param"""
        self.ap._command = Mock()

        self.ap.rate(0.3)

        self.ap._command.assert_called_with('/rate', 'POST', value=0.3)

    def test_stop(self):
        """Stop is sent as a POST"""
        self.ap._command = Mock()

        self.ap.stop()

        self.ap._command.assert_called_with('/stop', 'POST')

    def test_playback_info(self):
        """Playback Info is sent as a GET"""
        self.ap._command = Mock()

        self.ap.playback_info()

        self.ap._command.assert_called_with('/playback-info')

    def test_scrub_no_pos(self):
        """Scrub is sent as a GET when no position is provided and the return values are converted to floats"""

        rv = email.message_from_string("""duration: 83.124794\r\nposition: 14.467000""")

        self.ap._command = Mock(return_value=rv)

        result = self.ap.scrub()

        self.ap._command.assert_called_with('/scrub', 'GET')

        assert result['duration'] == 83.124794
        assert result['position'] == 14.467000

    def test_scrub_pos(self):
        """Scrub is sent as a POST when position is provided"""

        rv = email.message_from_string("""duration: 83.124794\r\nposition: 14.467000""")

        self.ap._command = Mock(return_value=rv)

        self.ap.scrub(91.6)

        calls = [call('/scrub', 'POST', position=91.6), call('/scrub', 'GET')]

        self.ap._command.assert_has_calls(calls)


class TestAirPlayDiscovery(unittest.TestCase):
    @patch('airplay.airplay.socket.socket')
    @patch('airplay.airplay.ServiceBrowser', new_callable=lambda: FakeServiceBrowser)
    @patch('airplay.airplay.Zeroconf', new_callable=lambda: FakeZeroconf)
    def test_timeout(self, zc, sb, sock):
        """When fast=False, find() always waits for the timeout to expire"""

        sb.name = 'test-device.foo.bar'
        sb.info = zc.info = Mock(address=socket.inet_aton('192.0.2.23'), port=916)

        start = time.time()
        devices = AirPlay.find(timeout=2, fast=False)
        assert time.time() - start > 2

        assert isinstance(devices[0], AirPlay)
        assert devices[0].name == 'test-device'
        assert devices[0].host == socket.inet_ntoa(zc.info.address)
        assert devices[0].port == zc.info.port

    @patch('airplay.airplay.socket.socket')
    @patch('airplay.airplay.ServiceBrowser', new_callable=lambda: FakeServiceBrowser)
    @patch('airplay.airplay.Zeroconf', new_callable=lambda: FakeZeroconf)
    def test_fast_results(self, zc, sb, sock):
        """When fast=True find() returns as soon as there is a result"""

        sb.name = 'test-short'
        sb.info = zc.info = Mock(address=socket.inet_aton('192.0.2.23'), port=916)

        start = time.time()
        devices = AirPlay.find(timeout=2, fast=True)

        assert time.time() - start < 2

        assert devices[0].name == sb.name
        assert devices[0].host == socket.inet_ntoa(zc.info.address)
        assert devices[0].port == zc.info.port

    @patch('airplay.airplay.socket.socket')
    @patch('airplay.airplay.ServiceBrowser', new_callable=lambda: FakeServiceBrowser)
    @patch('airplay.airplay.Zeroconf')
    def test_no_info(self, zc, sb, sock):
        """If zeroconf doesn't return info on the service, we don't store it"""

        sb.info = None

        start = time.time()
        devices = AirPlay.find(timeout=2, fast=True)
        assert time.time() - start > 2

        assert len(devices) == 0


class TestRangeHTTPServerStartUp(unittest.TestCase):
    def test_no_directories(self):
        """ValueError is raised if directory serving is attempted"""

        try:
            tempdir = tempfile.mkdtemp()

            def go():
                RangeHTTPServer.start([tempdir])

            self.assertRaises(ValueError, go)
        finally:
            os.rmdir(tempdir)

    def test_no_files_with_same_name(self):
        """ValueError is raised if two files with same name are served"""

        try:
            tempdir1 = tempfile.mkdtemp()
            tempdir2 = tempfile.mkdtemp()

            tempfn1 = os.path.join(tempdir1, 'foo.txt')
            tempfn2 = os.path.join(tempdir2, 'foo.txt')

            with open(tempfn1, 'w') as fh1:
                with open(tempfn2, 'w') as fh2:
                    fh1.write('foo')
                    fh2.write('foo')

            def go():
                RangeHTTPServer.start([tempfn1, tempfn2])

            self.assertRaises(ValueError, go)
        finally:
            os.remove(tempfn1)
            os.remove(tempfn2)

            os.rmdir(tempdir1)
            os.rmdir(tempdir2)


class TestRangeHTTPServerACL(unittest.TestCase):
    def setUp(self):

        # generate two test files
        self.data = b'abcdefghijklmnopqrstuvwxyz' * 1024

        fd, path = tempfile.mkstemp()
        os.write(fd, self.data)
        os.close(fd)
        self.testone = path
        self.pathone = '/' + os.path.basename(self.testone)

        fd, path = tempfile.mkstemp()
        os.write(fd, self.data)
        os.close(fd)
        self.testtwo = path
        self.pathtwo = '/' + os.path.basename(self.testtwo)

        fn1 = os.path.realpath(self.testone)
        fn2 = os.path.realpath(self.testtwo)

        self.allowed_filenames = {
            os.path.basename(fn1): fn1,
            os.path.basename(fn2): fn2,
        }

        self.server = Mock()

        self.client = ('127.0.0.1', 9160)

    def fake_request(self, path):
        self.http = RangeHTTPServer(FakeSocket(b''), self.client, self.server)
        self.http.handle = lambda x: None
        self.http.send_error = Mock()

        return self.http.check_path(path)

    def tearDown(self):
        try:
            os.remove(self.testone)
            os.remove(self.testtwo)
        except OSError:
            pass

    def test_allowed_host(self):
        """ValueError is raised if an unallowed host attempts access"""

        self.server = Mock(allowed_host='192.0.2.99')

        self.assertRaises(ValueError, self.fake_request, self.pathone)

        self.http.send_error.assert_called_with(400, 'Bad Request')

    def test_allowed_filename(self):
        """ValueError is raised if any other files are requested"""

        self.server = Mock(
            allowed_filenames=self.allowed_filenames,
            allowed_host='127.0.0.1'
        )

        result = self.fake_request(self.pathone)
        assert result[0] in self.allowed_filenames.values()

        result = self.fake_request(self.pathtwo)
        assert result[0] in self.allowed_filenames.values()

        self.assertRaises(ValueError, self.fake_request, '/../../../../../.././etc/passwd')
        self.http.send_error.assert_called_with(400, 'Bad Request')

        self.assertRaises(ValueError, self.fake_request, '/foo')
        self.http.send_error.assert_called_with(400, 'Bad Request')

    def test_file_open(self):
        """ValueError is raised if we cannot open or stat the file"""

        self.server = Mock(
            allowed_filenames=self.allowed_filenames,
            allowed_host='127.0.0.1'
        )

        # can't open file
        os.chmod(self.testone, 0000)
        self.assertRaises(ValueError, self.fake_request, self.pathone)
        self.http.send_error.assert_called_with(500, 'Internal Server Error')

        # file doesn't exist
        os.remove(self.testtwo)
        self.assertRaises(ValueError, self.fake_request, self.pathtwo)
        self.http.send_error.assert_called_with(500, 'Internal Server Error')


class TestRangeHTTPServerOSError(unittest.TestCase):
    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def setUp(self, mock):

        mock.sock = MockSocket()
        mock.sock.recv_data = """HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n"""

        self.data = b'abcdefghijklmnopqrstuvwxyz' * 1024
        fd, path = tempfile.mkstemp()
        os.write(fd, self.data)
        os.close(fd)
        self.testfile = path

        # patch our check function to return the expected data
        # but simulate the file disappaearing between the check
        # and when we re-open it for sending
        def no_check_path(self, *args, **kwargs):
            stats = os.stat(path)
            os.remove(path)

            return path, stats

        with patch('airplay.airplay.RangeHTTPServer.check_path', side_effect=no_check_path):
            self.ap = AirPlay('127.0.0.1', 916, 'test')
            self.test_url = self.ap.serve(path)[0]

        assert self.test_url.startswith('http://127.0.0.1')

    def tearDown(self):
        try:
            os.remove(self.testfile)
        except OSError:
            pass

    def test_os_error(self):
        """Problems reading the file after check return HTTP 500"""
        # mock out check_path to just return path
        # call path with an invalid file

        request = Request(self.test_url)

        error = None
        try:
            urlopen(request)
        except URLError as exc:
            error = exc
        except:
            pass

        assert error.code == 500


class TestLazyLoading(unittest.TestCase):
    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def setUp(self, mock):

        mock.sock = MockSocket()
        mock.sock.recv_data = """HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n"""

        try:
            import airplay.airplay as airplay
        except ImportError:
            import airplay

        # Is there a better way to test Lazy Imports than this?
        # mucking around in __dict__ feels gross, but nothing else seemed to work :/
        # tried mock.patch.dict with sys.modules
        # tried mock.patch.dict with airplay.__dict__
        # tried patch, but it doesn't have a delete option
        # needs to work with python 2 and python 3

        self.apnuke = {
            'Zeroconf': None,
            'ServiceBrowser': None
        }

        for thing in self.apnuke.keys():
            try:
                self.apnuke[thing] = airplay.__dict__[thing]
                del airplay.__dict__[thing]
            except KeyError:
                pass

        self.ap = airplay.AirPlay('127.0.0.1', 916, 'test')

    def tearDown(self):
        try:
            import airplay.airplay as airplay
        except ImportError:
            import airplay

        for kk, vv in self.apnuke.items():
            if vv is not None:
                airplay.__dict__[kk] = vv

    def test_find_no_zeroconf(self):
        """None is returned from find() if we dont have zeroconf installed"""

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert self.ap.find() is None


class TestRangeHTTPServer(unittest.TestCase):
    @patch('airplay.airplay.socket', new_callable=lambda: MockSocket)
    def setUp(self, mock):

        mock.sock = MockSocket()
        mock.sock.recv_data = """HTTP/1.1 501 Not Implemented\r\nContent-Length: 0\r\n\r\n"""

        self.ap = AirPlay('127.0.0.1', 916, 'test')

        self.data = b'abcdefghijklmnopqrstuvwxyz' * 1024

        fd, path = tempfile.mkstemp()
        os.write(fd, self.data)
        os.close(fd)

        self.test_url = self.ap.serve(path)[0]

        assert self.test_url.startswith('http://127.0.0.1')

        self.testfile = path

    def tearDown(self):
        os.remove(self.testfile)

    def test_no_multiple_ranges(self):
        """Multiple Ranges are not supported"""

        request = Request(self.test_url)
        request.add_header('range', 'bytes=1-4,9-90')

        error = None
        try:
            urlopen(request)
        except URLError as exc:
            error = exc

        assert error.code == 400

    def test_unsatisfiable_range(self):
        """Range requests out of bounds return HTTP 416"""

        request = Request(self.test_url)

        # make our request start past the end of our file
        first = len(self.data) + 1024

        request.add_header('range', 'bytes={0}-'.format(first))

        error = None
        try:
            urlopen(request)
        except URLError as exc:
            error = exc

        assert error.code == 416

    def test_bad_range(self):
        """Malformed (not empty) range requests return HTTP 400"""

        request = Request(self.test_url)
        request.add_header('range', 'bytes=2-1')

        error = None
        try:
            urlopen(request)
        except URLError as exc:
            error = exc

        assert error.code == 400

    def test_full_get(self):
        """When we make a GET request with no Range header, the entire file is returned"""
        request = Request(self.test_url)

        assert self.data == urlopen(request).read()

    def test_range_get(self):
        """When we make a GET request with a Range header, the proper chunk is returned with appropriate headers"""
        request = Request(self.test_url)
        request.add_header('range', 'bytes=1-4')

        response = urlopen(request)
        msg = response.info()

        assert int(msg['content-length']) == 4
        assert msg['content-range'] == "bytes 1-4/{0}".format(len(self.data))

        assert self.data[1:5] == response.read()

    def test_head(self):
        """When we make a HEAD request, no body is returned"""
        request = Request(self.test_url)
        request.get_method = lambda: 'HEAD'

        response = urlopen(request)
        msg = response.info()

        # HEAD should return no body
        assert response.read() == b''

        # we should get the proper content-header back
        assert int(msg['content-length']) == len(self.data)


class TestFFmpeg(unittest.TestCase):
    def setUp(self):
        self.work_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.work_dir)

    def test_bad_path_ffmpeg(self):
        """if ffmpeg does not exist, EncoderNotInstalledError is raised"""
        def go():
            FFmpeg(ffmpeg=os.path.join(self.work_dir, 'ffmpeg'), ffprobe='true')

        self.assertRaises(EncoderNotInstalledError, go)

    def test_bad_path_ffprobe(self):
        """if probe does not exist, EncoderNotInstalledError is raised"""
        def go():
            FFmpeg(ffmpeg='true', ffprobe=os.path.join(self.work_dir, 'ffprobe'))

        self.assertRaises(EncoderNotInstalledError, go)

    def test_old_ffmpeg(self):
        """If ffmpeg returns 1 during the test, EncoderNotInstalledError is raised"""
        def go():
            FFmpeg(ffmpeg='false', ffprobe='true')

        self.assertRaises(EncoderNotInstalledError, go)

    def test_old_ffprobe(self):
        """If ffprobe returns 1 during the test, MediaParseError is raised"""
        def go():
            FFmpeg(ffmpeg='true', ffprobe='false')

        self.assertRaises(EncoderNotInstalledError, go)

    def test_bad_encoder(self):
        """If ffmpeg exists, but outputs unexpected info, we bail with details"""

        # TODO: make this work on windows
        FAKE_FFPROBE = """#!/bin/sh\n echo '{"streams": [{"codec_name": "h264", "codec_type": "video"}, {"codec_name": "invalid", "codec_type": "audio"}], "format": {"format_name": "mpegts"}}'"""  # NOQA

        ffp = os.path.join(self.work_dir, 'fake_ffprobe')

        with open(ffp, 'w') as fh:
            fh.write(FAKE_FFPROBE)

        os.chmod(ffp, 0o0700)

        def go():
            FFmpeg(ffmpeg='true', ffprobe=ffp)

        self.assertRaises(AssertionError, go)

    def test_run_quiet(self):
        """When _run is called with quiet=True no stderr is produced"""
        pass

    def test_run_loud(self):
        """When _run is called with quiet=Flse, stderr is produced"""
        pass

    def test_ffprobe_bad_file(self):
        """When ffprobe returns an error, or invalid JSON, MediaParseError is raised"""
        pass

    def test_ffprobe_good_file(self):
        """When ffprobe returns 0 and valid JSON a simplified object is returned"""
        pass

    def test_segment_single_file(self):
        """A single file can be segmented"""
        pass

    def test_segment_multiple_files(self):
        """Multiple files can be segmented"""
        pass

    def test_segment_output_opts(self):
        """If specified, we can control the output dir and file names used"""
        pass

    def test_segment_invalid_input(self):
        """If an invalid input is provided, MediaParseError is raised"""
        pass

    def test_segment_invalid_output_dir(self):
        """If an invalid output directory is provided, ValueError is raised"""
        pass


class FakeZeroconf(object):
    def __init__(self, info=None):
        self.info = info

    def get_service_info(self, *args, **kwargs):
        return self.info

    def close(self):
        pass


class FakeServiceBrowser(object):
    name = 'fake-service.local'
    info = None

    def __init__(self, *args, **kwargs):
        self.handler = kwargs.get('handlers')[0]
        self.handler(FakeZeroconf(self.info), args[1], self.name, ServiceStateChange.Added)


class MockSocket(object):
    sock = None
    timeout = None
    error = socket.error
    AF_INET = socket.AF_INET
    SOCK_STREAM = socket.SOCK_STREAM

    def __init__(self, *args, **kwargs):
        self.send_data = ''
        self.recv_data = ''

    def recv(self, *args, **kwargs):
        try:
            basestring
        except NameError:
            basestring = str

        if isinstance(self.recv_data, basestring):
            data = self.recv_data
        else:
            try:
                data = self.recv_data.pop(0)
            except IndexError:
                raise socket.timeout

        try:
            return bytes(data, 'UTF-8')
        except TypeError:
            return data

    def send(self, data, **kwargs):
        self.send_data = data

    def connect(self, *args, **kwargs):
        pass

    def close(self, *args, **kwargs):
        pass

    def settimeout(self, *args, **kwargs):
        pass

    def getpeername(self, *args, **kwargs):
        return ('192.0.2.23', 9160)

    def getsockname(self, *args, **kwargs):
        return ('127.0.0.1', 9160)

    @classmethod
    def socket(cls, *args, **kwargs):
        return cls.sock
