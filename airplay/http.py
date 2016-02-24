import os
import posixpath
import socket
import SocketServer
import sys
import urllib

from BaseHTTPServer import BaseHTTPRequestHandler

import httpheader


# Work around a bug in some versions of Python's SocketServer :(
# http://bugs.python.org/issue14574
def finish_fix(self, *args, **kwargs):
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
    def start(cls, filename, allowed_host=None, queue=None):
        """Start a SocketServer.TCPServer using this class to handle requests

        Args:
            filename(str):  An absolute path to a single file to server
                            Access will only be granted to this file

            allowed_host(str, optional):    If provided, only this host will
                                            be allowed to access the server

            queue(Queue.Queue, optional):   If provided, the host/port the server
                                            binds to will be put() into this queue

        """
        os.chdir(os.path.dirname(filename))

        httpd = SocketServer.TCPServer(('', 0), cls)
        httpd.allowed_filename = filename
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

    def handle(self):
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

        # get full path to file requested
        path = posixpath.normpath(urllib.unquote(path))
        path = os.path.join(os.getcwd(), path.lstrip('/'))

        # if we have an allowed host, then only allow access from it
        if self.server.allowed_host and self.client_address[0] != self.server.allowed_host:
            self.send_error(400, "Bad Request")
            raise ValueError('Client is not allowed')

        # don't do directory indexing
        if os.path.isdir(path):
            self.send_error(400, "Bad Request")
            raise ValueError("Requested path is a directory")

        # if they try to request something else, don't serve it
        if path != self.server.allowed_filename:
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
