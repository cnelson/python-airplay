# python-airplay [![Build Status](https://travis-ci.org/cnelson/python-airplay.svg?branch=master)](https://travis-ci.org/cnelson/python-airplay) 

A python client for the Video portions of the [AirPlay Protocol](https://nto.github.io/AirPlay.html#video)

## Install
    
    $ pip install https://github.com/cnelson/python-airplay/archive/master.zip

## Using

If you just want to stream video to your AirPlay device, this package ships
a command line interface named 'airplay'.  It provides help:

    usage: airplay [-h] [--position POSITION] [--atv ATV] path

    Playback a local or remote video file via AirPlay. This does not do any on-
    the-fly transcoding (yet), so the file must already be suitable for the
    AirPlay device.

    positional arguments:
    path                  An absolute path or URL to a video file

    optional arguments:
    -h, --help            show this help message and exit
    --position POSITION, --pos POSITION, -p POSITION
                          Where to being playback [0.0-1.0]
    --atv ATV             Playback video to a specific AppleTV
                          [<host/ip>:(<port>)]

If you want to use this library in your python project, the [source for the cli script](airplay/cli.py) is a good example of how to use this library.

All classes and methods are fully documented, and a [full test suite](airplay/tests.py) is provided.
