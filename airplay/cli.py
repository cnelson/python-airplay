import argparse
import os
import time

from airplay import AirPlay, FFmpeg, MediaParseError, EncoderNotInstalledError

import click


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


def main():
    parser = argparse.ArgumentParser(
        description="Playback a local or remote video file via AirPlay. "
                    "This does not do any on-the-fly transcoding (yet), "
                    "so the file must already be suitable for the AirPlay device.",
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

    args = parser.parse_args()

    # connect to the AirPlay device we want to control
    try:
        ap = get_airplay_device(args.device)
    except (ValueError, RuntimeError) as exc:
        parser.error(exc)

    target = args.path

    if not args.force:
        # if they gave us custom paths to an encoder, then die if they
        # are wrong
        try:
            if args.ffmpeg != 'ffmpeg' or args.ffprobe != 'ffprobe':
                ap.encoder = FFmpeg(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        except EncoderNotInstalledError as exc:
            parser.exit(exc)

        try:
            if not ap.can_play(target):
                target = ap.convert(target)
                target = list(target)
        except EncoderNotInstalledError:
            print("Encoder not installed, skipping conversion")
        except MediaParseError:
            parser.exit("Unkonwn input format.  Use --force if you are sure your AirPlay server can play it")

    # if the url is on our local disk, then we need to spin up a server to start it
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
