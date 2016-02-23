import argparse
import time

from airplay import AirPlay

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
        raise RuntimeError('No AppleTV could be found.  Use --atv to manually specify an AppleTV')
    elif len(devices) == 1:
        return devices[0]
    elif len(devices) > 1:
        error = "Multiple AppleTVs were found.  Use --atv to select a specific one.\n\n"
        error += "Available Apple TVs:\n"
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
        description="Playback a remote video file via AirPlay. "
                    "This does not do any on-the-fly transcoding (yet), "
                    "so the file must already be suitable for the AirPlay device."
    )

    parser.add_argument('path', help='A URL to a video file')
    parser.add_argument('--position', '--pos', '-p', default=0.0, type=float, help='Where to being playback [0.0-1.0]')
    parser.add_argument('--atv', default=None, help='Playback video to a specific AppleTV [<host/ip>:(<port>)]')
    args = parser.parse_args()

    # connect to the AirPlay device we want to control
    try:
        ap = get_airplay_device(args.atv)
    except (ValueError, RuntimeError) as exc:
        parser.error(exc)

    duration = 0
    position = 0
    state = 'loading'

    # play what they asked
    ap.play(args.path, args.position)

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
