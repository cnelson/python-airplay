import argparse
import os
import time

from .airplay import AirPlay

from .ffmpeg import FFmpeg, MediaParseError, EncoderNotInstalledError

import click
import youtube_dl


def get_airplay_device(hostport):
    if hostport is not None:
        try:
            (host, port) = hostport.split(':', 1)
            port = int(port)
        except ValueError:
            host = hostport
            port = 7000

        return AirPlay(host, port)

    devices = AirPlay.find(fast=True)

    if len(devices) == 0:
        raise RuntimeError('No AirPlay devices were found.  Use --device to manually specify an device.')
    elif len(devices) == 1:
        return devices[0]
    elif len(devices) > 1:
        error = "Multiple AirPlay devices were found.  Use --device to select a specific one.\n\n"
        error += "Available AirPlay devices:\n"
        error += "--------------------\n"
        for dd in devices:
            error += "\t* {0}: {1}:{2}\n".format(dd.name, dd.host, dd.port)

        raise RuntimeError(error)


def humanize_seconds(secs):
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)

    return "%02d:%02d:%02d" % (h, m, s)


def youtubedl(target, fmt='best[ext=mp4]/bestvideo+bestaudio'):
    urls = []
    try:
        ydl = youtube_dl.YoutubeDL({'format': fmt})
        info = ydl.extract_info(target, download=False)

        if 'entries' in info:
            info = info['entries'][0]

        for fid in info['format_id'].split('+'):
            for ff in info['formats']:
                if ff['format_id'] == fid:
                    urls.append(ff['url'])

    except youtube_dl.utils.DownloadError:
        pass

    return urls


def main():
    parser = argparse.ArgumentParser(
        description="Playback a local or remote video file via AirPlay. "
                    "If ffmpeg and ffprobe are available, video will automatically "
                    "be converted to work with your AirPlay device if needed. "
                    "Static builds of these tools are available at "
                    "https://ffmpeg.org/download.html",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        'path',
        help='An absolute path or URL to a video file'
    )

    parser.add_argument(
        '--position',
        '--pos',
        '-p',
        default=0.0,
        type=float,
        help='Where to being playback [0.0-1.0]'
    )

    parser.add_argument(
        '--device',
        '--dev',
        '-d',
        default=None,
        help='Playback video to a specific device [<host/ip>:(<port>)]'
    )
    parser.add_argument(
        '--force',
        '-f',
        default=False,
        action='store_true',
        help='Force playback of path as given. Do not attempt parsing or conversion'
    )

    parser.add_argument(
        '--ffmpeg',
        default='ffmpeg',
        help='The ffmpeg binary to use for conversion (if needed)'
    )
    parser.add_argument(
        '--ffprobe',
        default='ffprobe',
        help='The ffprobe binary to use for parsing (if needed)'
    )

    parser.add_argument(
        '--tmpdir',
        default=None,
        help='Use this temp directory when converting files'
    )

    args = parser.parse_args()

    # connect to the AirPlay device we want to control
    try:
        ap = get_airplay_device(args.device)
    except (ValueError, RuntimeError) as exc:
        parser.error(exc)

    # now figure out what we want to playback
    target = args.path

    # if they told us to force it, skip all the checking
    if not args.force:

        # bail if they gave us custom ffmpeg settings and they are fubar
        try:
            if args.ffmpeg != 'ffmpeg' or args.ffprobe != 'ffprobe':
                ap.encoder = FFmpeg(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        except EncoderNotInstalledError as exc:
            parser.exit(exc)

        try:
            # see if we can play the url they gave us
            if not ap.can_play(target):
                # if not, convert it
                target = ap.convert(target, tmpdir=args.tmpdir)
        except EncoderNotInstalledError:
            # try to play anyway if the encoder isn't installed, it cant hurt
            print("Encoder not installed, playback may not be successful.")
        except MediaParseError:
            # we have encoders installed, but can't understand the file
            # see if it's a non-video url and youtubedl can do it for us
            urls = youtubedl(target)

            # nothing back? youtubedl doesn't know how to deal with it
            if len(urls) == 0:
                parser.exit("Unknown input format. Use --force if you are sure your AirPlay device an play it.")

            # If we got a single file back, and we can play it, we don't need to do anything
            if len(urls) == 1 and ap.can_play(urls[0]):
                target = urls[0]
            else:
                # multiple urls we need to mux them
                target = ap.convert(urls, tmpdir=args.tmpdir)

    # if the resovled playback target is local, then we need to spin up
    # a server to deliver it to the AirPlay device
    # (if it's a list of files, then it's from the encoder and local)
    if isinstance(target, list) or os.path.exists(target):
        target = ap.serve(target)[0]

    duration = 0
    position = 0
    state = 'loading'

    # play what they asked
    ap.play(target, args.position)

    # stay in this loop until we exit
    with click.progressbar(length=100, show_eta=False) as bar:
        try:
            while True:
                for ev in ap.events(block=False):
                    newstate = ev.get('state', None)

                    if newstate is None:
                        continue

                    if newstate == 'playing':
                        duration = ev.get('duration')
                        position = ev.get('position')

                    state = newstate

                if state == 'stopped':
                    raise KeyboardInterrupt

                bar.label = state.capitalize()

                if state == 'playing':
                    info = ap.scrub()
                    duration = info['duration']
                    position = info['position']

                if state in ['playing', 'paused']:
                    bar.label += ': {0} / {1}'.format(
                        humanize_seconds(position),
                        humanize_seconds(duration)
                    )
                    try:
                        bar.pos = int((position / duration) * 100)
                    except ZeroDivisionError:
                        bar.pos = 0

                bar.label = bar.label.ljust(28)
                bar.render_progress()

                time.sleep(.5)

        except KeyboardInterrupt:
            ap = None
            raise SystemExit


if __name__ == '__main__':
    main()
