import os
import posixpath
import socket
import sys

try:
    from BaseHTTPServer import BaseHTTPRequestHandler
except ImportError:
    from http.server import BaseHTTPRequestHandler

try:
    import SocketServer
except ImportError:
    import socketserver as SocketServer

try:
    from urllib import unquote
except ImportError:
    from urllib.parse import unquote

from .vendor import httpheader


# Work around a bug in some versions of Python's SocketServer :(
# http://bugs.python.org/issue14574
def finish_fix(self, *args, **kwargs):  # pragma: no cover
    try:
        if not self.wfile.closed:
            self.wfile.flush()
            self.wfile.close()
    except socket.error:
        pass
    self.rfile.close()

SocketServer.StreamRequestHandler.finish = finish_fix


class RangeHTTPServer(BaseHTTPRequestHandler):
    """This is a simple HTTP server that can be used to serve content to AirPlay devices.

    It supports *single* Range requests which is all (it seems) is required.
    """
    @classmethod
    def start(cls, paths=[], allowed_host=None, queue=None):
        """Start a SocketServer.TCPServer using this class to handle requests

        Args:
            paths(list):     A list of abosolute paths to files to serve.
                                Only access to these files will be allowed.
                                Directories are not permitted.

            allowed_host(str, optional):    If provided, only this host will
                                            be allowed to access the server

            queue(Queue.Queue, optional):   If provided, the host/port the server
                                            binds to will be put() into this queue

        Raises:
            ValueError:     There was an issue with the provided paths

        """

        allowed_files = {}

        for fn in list(set(paths)):
            fn = os.path.realpath(fn)

            if os.path.isdir(fn):
                raise ValueError("Directories cannot be served. {0}".format(fn))

            bn = os.path.basename(fn)

            if bn in allowed_files:
                raise ValueError('Cannot serve two files with the same name in different directories')
                # If you are reading this, we've clearly made an invalid assumption. Let's get you
                # caught up:

                # We have a couple of critical requirements for this server:

                # Requirement 1 (obviously): Don't serve anything except the files the caller
                # specifically allows.  We really don't want to start a buggy server that ends up
                # being responsible for a data exfil when all we are supposed to do is play video!

                # Requirement 2: Expose as little information to the AirPlay server as possible.
                # In many cases we blindly connect to whomeever is announcing AirPlay services via
                # Bonjour so we must assume the AirPlay device is an attacker.

                # Knowing this, we don't want to take the easy approach and have our URLs be:
                # http://<host>:<port>/<path> where path is the abosolute path to the file we want
                # want to serve. While we have code that protects from an attacker accessing any
                # file via a request to http://host/etc/passwd (for example) we don't want to expose
                # any information about our file system layout if we can help it.

                # My first thought to serve this was to send the AirPlay server hashes and then look
                # up the the real path to the file and serve it.  In psuedo-y code:

                # allowed_filenames[hash(fn)] = fn
                # URL == http://<host>:<port>/<hash>

                # However when we need to serve HLS segments this breaks as ffmpeg generates the
                # index for us, and while we can control the filenames it uses, we can't get it to
                # write the ts file to 'foo.ts', but write hash('foo.ts') to the index.

                # So, here we are with this janky solution which should have worked since we only
                # planned on serving a single video file, or two HLS files with names that will be
                # different.

                # Given that you are reading this, the above is now probably an invalid assumption
                # So, do you have a better idea?  Hit me up:

                # https://github.com/cnelson/python-airplay/issues

            allowed_files[bn] = fn

        httpd = SocketServer.TCPServer(('', 0), cls)
        httpd.allowed_filenames = allowed_files
        httpd.allowed_host = allowed_host

        if queue:
            queue.put(httpd.server_address)

        # BaseHTTPServer likes to log requests to stderr/out
        # drop all that nose
        with open('/dev/null', 'w') as fh:
            sys.stdout = sys.stderr = fh
            try:
                httpd.serve_forever()
            except:  # NOQA
                pass

    def handle(self):   # pragma: no cover
        """Handle requests.

        We override this because we need to work around a bug in some
        versions of Python's SocketServer :(

        See http://bugs.python.org/issue14574
        """

        self.close_connection = 1

        try:
            self.handle_one_request()
        except socket.error as exc:
            if exc.errno == 32:
                pass

    def do_HEAD(self):
        """Handle a HEAD request"""
        try:
            path, stats = self.check_path(self.path)
        except ValueError:
            return

        self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", stats.st_size)
        self.end_headers()

    def do_GET(self):
        """Handle a GET request with some support for the Range header"""
        try:
            path, stats = self.check_path(self.path)
        except ValueError:
            return

        # assume we are sending the whole file first
        ranges = None
        first = 0
        last = stats.st_size

        # but see if a Range: header tell us differently
        try:
            ranges = httpheader.parse_range_header(self.headers.get('range', ''))
            ranges.fix_to_size(stats.st_size)
            ranges.coalesce()

            if not ranges.is_single_range():
                self.send_error(400, "Multiple ranges not supported :(")
                return

            first = ranges.range_specs[0].first
            last = ranges.range_specs[0].last + 1
        except httpheader.ParseError:
            pass
        except httpheader.RangeUnsatisfiableError:
            self.send_error(416, "Requested range not possible")
            return
        except ValueError:
            # this can get raised if the Range request is weird like bytes=2-1
            # not sure why this doesn't raise as a ParseError, but whatevs
            self.send_error(400, "Bad Request")
            return

        try:
            with open(path, 'rb') as fh:
                if ranges is None:
                    self.send_response(200)
                else:
                    self.send_response(206)
                    self.send_header(
                        "Content-Range",
                        'bytes ' + str(first) + '-' + str(last - 1) + '/' + str(stats.st_size)
                    )

                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", last - first)
                self.end_headers()

                # send the chunk they asked for
                # possibly the whole thing!
                buffer_size = 8192

                fh.seek(first, 0)
                while buffer_size > 0:

                    if first + buffer_size > last:
                        buffer_size = last - first
                    try:
                        self.wfile.write(fh.read(buffer_size))
                    except socket.error:
                        break

                    first = first + buffer_size
        except EnvironmentError:
            self.send_error(500, "Internal Server Error")
            return

    def check_path(self, path):
        """Verify that the client and server are allowed to access `path`

        Args:
            path(str): The path from an HTTP rqeuest, it will be joined to os.getcwd()

        Returns:
            (str, stats):    An abosolute path to the file on disk, and the result of os.stat()

        Raises:
            ValueError:     The path could not be accessed (exception will say why)
        """

        # if we have an allowed host, then only allow access from it
        if self.server.allowed_host and self.client_address[0] != self.server.allowed_host:
            self.send_error(400, "Bad Request")
            raise ValueError('Client is not allowed')

        # get full path to file requested
        path = posixpath.normpath(unquote(path)).lstrip('/')
        try:
            path = self.server.allowed_filenames[path]
        except KeyError:
            self.send_error(400, "Bad Request")
            raise ValueError("Requested path was not in the allowed list")

        # make sure we can stat and open the file
        try:
            stats = os.stat(path)
            fh = open(path, 'rb')
        except (EnvironmentError) as exc:
            self.send_error(500, "Internal Server Error")
            raise ValueError("Unable to access the path: {0}".format(exc))
        finally:
            try:
                fh.close()
            except NameError:
                pass

        return path, stats
