import subprocess
import tempfile
import os
import shutil
import json


class EncoderNotInstalledError(EnvironmentError):
    """Raised if we can't find ffmpeg or ffprobe"""
    pass


class MediaParseError(ValueError):
    """Raised if ffprobe or ffmpeg cannot parse an input file"""
    pass


class FFmpeg(object):
    """Use ffmpeg / ffprobe to convert video files so they are suitable for
    playback on an AirPlay device
    """
    def __init__(self, ffmpeg='ffmpeg', ffprobe='ffprobe'):
        """Ensure ffmpeg and ffprobe are both executable and the correct versions

        Args:
            ffmpeg (str):   The path to an ffmpeg binary.  Defaults to looking for 'ffmpeg' in your path.
            ffprobe (str):  The path to an ffprobe binary. Defaults to looking for 'ffprobe' in your path.

        Raises:
            EncoderNotInstalledError:   ffmpeg / ffprobe were not executable or not the correct version
        """

        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

        self._test()

    def _run(self, cmd, quiet=True):
        """Execute cmd with stderr redirected, and common exceptions converted

        Args:
            cmd (list):     This is passed directly to subprocess.check_output()
            quiet (bool):   If True, stderr output is supressed, if False, it will be included.

        Returns:
            The stdout produced by `cmd`, and possibly the stderr (if quiet is False)

        Raises:
            EncoderNotInstalledError:   The `cmd[0]` command could not be executed.
            subprocess.CalledProcessError: An error occurred while executing `cmd`
        """
        try:
            if quiet:
                stderr = open(os.devnull, 'wb')
            else:
                stderr = subprocess.STDOUT

            return subprocess.check_output(cmd, stderr=stderr)
        except KeyboardInterrupt:
            return
        except OSError:
            raise EncoderNotInstalledError("Cannot execute {0}".format(cmd[0]))
        finally:
            try:
                stderr.close()
            except (NameError, AttributeError):
                pass

    def probe(self, path):
        """Probe `path` to determine it's file format

        Args:
            path (str): An absolute path to a file or URL to probe

        Returns:
            tuple: The container format and a list of streams.  Like:
            (u'mov,mp4,m4a,3gp,3g2,mj2', [(u'video', u'h264'), (u'audio', u'aac')])

        Raises:
            MediaParseError:    Unable to parse the file
            EncoderNotInstalledError: ffprobe could not be executed.

        """
        probe_opts = [
            '-print_format', 'json',    # output JSON
            '-v', 'quiet',              # suppress all non-JSON output
            '-show_format',             # container information
            '-show_streams',            # track information
            path
        ]

        try:
            output = self._run([self.ffprobe] + probe_opts)

            try:
                output = str(output, 'UTF-8')
            except TypeError:
                pass

            info = json.loads(
                output
            )
        except (ValueError, subprocess.CalledProcessError):
            raise MediaParseError("Unknown input format: {0}".format(path))

        streams = []
        for ss in info['streams']:
            streams.append((ss['codec_type'], ss['codec_name']))

        return info['format']['format_name'], streams

    def segment(self,
                paths,
                output_directory=None,
                index='airplay.m3u8',
                transport_stream='airplay.ts',
                options=[]):
        """Create an HLS index and transport stream for `paths` in `output_directory

        Args:
            paths (list):           A list of one or more input files or URLs which will be combined
                                    into the HLS segment.
            output_directory (str): A path to a directory to write the HLS segments into.
                                    If not specified tempfile.mkdtemp() will be used
            index (str):            The name of the HLS index file to create.
            transport_stream (str): The name of the transport stream file to create
            options (list):         Additional options that will be passed to the ffmpeg process

        Returns
            tuple (index, transport_stream): Absoulte paths to the index and transport stream files

        Raises:
            ValueError:         An output directory was given but does not exist
            MediaParseError:    Unable to parse one of the input paths
            EncoderNotInstalledError:   ffmpeg could not be executed or was not the correct version
        """

        if not isinstance(paths, list):
            paths = [paths]

        if output_directory is None:
            output_directory = tempfile.mkdtemp()
        else:
            if not os.path.exists(output_directory):
                raise ValueError('{0} does not exist!'.format(output_directory))

        index = os.path.join(output_directory, index)
        transport_stream = os.path.join(output_directory, transport_stream)

        inputs = []

        # convert our paths from 'foo' to '-i foo'
        for ii in paths:
            # hack to allow specifying internal inputs to ffmpeg
            # we only really support this for the _test function
            if ii.startswith('LAVFI-'):
                ff, ii = ii.split('-', 1)

                inputs += ['-f', ff.lower()]
                ii = ii.lower()

            inputs += ['-i', ii]

        ffmpeg_opts = inputs + [
            '-hls_flags', 'single_file',                # write all data to one .ts file
            '-hls_list_size', '0',                      # infinite segment list size
            '-hls_allow_cache', '1',                    # allow client caching
            '-hls_segment_filename', transport_stream   # where to write the video
        ] + options

        try:
            self._run([self.ffmpeg] + ffmpeg_opts + [index], quiet=False)
        except subprocess.CalledProcessError as exc:
            # ffmpeg always exits with code 1 if error happened
            # this is our only hint it was a problem with the file
            emsg = 'Invalid data found when processing input'
            try:
                emsg = bytes(emsg, 'UTF-8')
            except TypeError:
                pass
            if emsg in exc.output:
                raise MediaParseError("Unknown input format: {0}".format(paths))
            else:
                raise EncoderNotInstalledError("{0} failed. It must be at least version 3.0.".format(self.ffmpeg))

        return index, transport_stream

    def _test(self):
        """Self test.  Ensure the given ffmpeg and ffprobe binaries are executable and
        produce the output we expect

        Returns:
            True:   Everything is good to go.

        Raises:
            EncoderNotInstalledError:   One of the provided binaries is not executable or
                                        an incorrect version
        """
        try:
            # segment one frame of an internal source
            work_dir = tempfile.mkdtemp()
            index, transport_stream = self.segment(
                ['LAVFI-TESTSRC', 'LAVFI-ANULLSRC'],
                output_directory=work_dir,
                options=['-vframes', '1']
            )

            # make sure we can inspect it
            container, streams = self.probe(transport_stream)

            # and that it's the format we expect
            assert container == 'mpegts'
            assert streams[0][1] == 'h264'
            assert streams[1][1] == 'aac'
        except MediaParseError:
            raise EncoderNotInstalledError('ffmpeg/ffprobe must be at least version 3.0')
        except AssertionError:
            raise AssertionError(
                'Unexpected file format issues, please report this at '
                'https://github.com/cnelson/python-airplay/issues{0}{1}{2}'.format(os.linesep, container, streams)
            )
        finally:
            shutil.rmtree(work_dir)

        return True
