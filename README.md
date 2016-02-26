# python-airplay [![Build Status](https://travis-ci.org/cnelson/python-airplay.svg?branch=master)](https://travis-ci.org/cnelson/python-airplay) 

A python client for the Video portions of the [AirPlay Protocol](https://nto.github.io/AirPlay.html#video).  


## Install

This package is not on PyPI (yet) so install from this repo:

    $  pip install https://github.com/cnelson/python-airplay/archive/master.zip


## I want to control my AirPlay device from the command line

Easy!

    # play a remote video file
    $ airplay http://clips.vorwaerts-gmbh.de/big_buck_bunny.mp4

    # play a remote video file, but start it half way through
    $ airplay -p 0.5 http://clips.vorwaerts-gmbh.de/big_buck_bunny.mp4

    # play a local video file
    $ airplay /path/to/some/local/file.mp4

    # or play to a specific device
    $ airplay --device 192.0.2.23:7000 http://clips.vorwaerts-gmbh.de/big_buck_bunny.mp4

    $ airplay --help
    usage: airplay [-h] [--position POSITION] [--device DEVICE] path

    Playback a local or remote video file via AirPlay. This does not do any on-
    the-fly transcoding (yet), so the file must already be suitable for the
    AirPlay device.

    positional arguments:
      path                  An absolute path or URL to a video file

    optional arguments:
      -h, --help            show this help message and exit
      --position POSITION, --pos POSITION, -p POSITION
                            Where to being playback [0.0-1.0]
      --device DEVICE, --dev DEVICE, -d DEVICE
                            Playback video to a specific device
                            [<host/ip>:(<port>)]



## I want to use this package in my own application

Awesome!  This package is compatible with Python >= 2.7 (including Python 3!)

    # Import the AirPlay class
    >>> from airplay import AirPlay

    # If you have zeroconf installed, the find() classmethod will locate devices for you
    >>> AirPlay.find(fast=True)
    [<airplay.airplay.AirPlay object at 0x1005d9a90>]

    # or you can manually specify a host/ip and optionally a port
    >>> ap = AirPlay('192.0.2.23')
    >>> ap = AirPlay('192.0.2.3', 7000)

    # Query the device
    >>> ap.server_info()
    {'protovers': '1.0', 'deviceid': 'FF:FF:FF:FF:FF:FF', 'features': 955001077751, 'srcvers': '268.1', 'vv': 2, 'osBuildVersion': '13U717', 'model': 'AppleTV5,3', 'macAddress': 'FF:FF:FF:FF:FF:FF'}

    # Play a video
    >>> ap.play('http://clips.vorwaerts-gmbh.de/big_buck_bunny.mp4')
    True

    # Get detailed playback information
    >>> ap.playback_info()
    {'duration': 60.095, 'playbackLikelyToKeepUp': True, 'readyToPlayMs': 0, 'rate': 1.0, 'playbackBufferEmpty': True, 'playbackBufferFull': False, 'loadedTimeRanges': [{'start': 0.0, 'duration': 60.095}], 'seekableTimeRanges': [{'start': 0.0, 'duration': 60.095}], 'readyToPlay': 1, 'position': 4.144803403}

    # Get just the playhead position
    >>> ap.scrub()
    {'duration': 60.095001, 'position': 12.465443}

    # Seek to an absolute position
    >>> ap.scrub(30)
    {'duration': 60.095001, 'position': 30.0}

    # Pause playback
    >>> ap.rate(0.0)
    True

    # Resume playback
    >>> ap.rate(1.0)
    True
 
    # Stop playback completely
    >>> ap.stop()
    True

    # Start a webserver to stream a local file to an AirPlay device
    >>> ap.serve('/tmp/home_movie.mp4')
    'http://192.0.2.114:51058/home_movie.mp4'

    # Playback the generated URL
    >>> ap.play('http://192.0.2.114:51058/home_movie.mp4')
    True

    # Read events from a generator as the device emits them
    >>> for event in ap.events():
    ...   print(event)
    ... 
    {'category': 'video', 'state': 'loading', 'sessionID': 349}
    {'category': 'video', 'state': 'paused', 'sessionID': 349}
    {'category': 'video', 'state': 'playing', 'params': {'duration': 60.095, 'readyToPlay': 1, 'playbackLikelyToKeepUp': True, 'playbackBufferEmpty': True, 'playbackLikelyToKeepUpTime': 0.0, 'position': 0.0, 'playbackBufferFull': False, 'seekableTimeRanges': [{'duration': 60.095, 'start': 0.0}], 'loadedTimeRanges': [{'duration': 60.095, 'start': 0.0}], 'rate': 1.0}, 'sessionID': 349}
    {'category': 'video', 'sessionID': 349, 'type': 'currentItemChanged'}
    {'category': 'video', 'state': 'loading', 'sessionID': 349}
    {'category': 'video', 'reason': 'ended', 'sessionID': 349, 'state': 'stopped'}

## API Documentation

### AirPlay(self, host, port=7000, name=None, timeout=5)

Connect to an AirPlay device

    >>> ap = AirPlay('hostname')
    >>> ap
    <airplay.airplay.AirPlay object at 0x102105630>

#### Arguments
* **host (str):**       Hostname or IP address of the device to connect to
* **port (int):**       Port to use when connectiong
* **name (str):**       Optional. The name of the device
* **timeout (int):**    Optional. A timeout for socket operations


#### Raises
* **ValueError:**     Unable to connect to the device on specified host/port

### Class Methods

### AirPlay.find(timeout=10, fast=False)

Discover AirPlay devices using Zeroconf/Bonjour

    >>> AirPlay.find(fast=True)
    [<airplay.airplay.AirPlay object at 0x1027e1be0>]


#### Arguments
* **timeout (int):** The number of seconds to wait for responses. If fast is False, then this function will always block for this number of seconds.
            
* **fast (bool):**    If True, do not wait for timeout to expire return as soon as we've found at least one AirPlay device.

#### Returns
* **list:**     A list of AirPlay objects; one for each AirPlay device found
* **None:**     The [zeroconf](https://pypi.python.org/pypi/zeroconf) package is not installed  


### Methods

### server_info()

Fetch general information about the AirPlay device

    >>> ap.server_info()
    {'protovers': '1.0', 'deviceid': 'FF:FF:FF:FF:FF:FF', 'features': 955001077751, 'srcvers': '268.1', 'vv': 2, 'osBuildVersion': '13U717', 'model': 'AppleTV5,3', 'macAddress': 'FF:FF:FF:FF:FF:FF'}

#### Returns
* **dict**: key/value pairs that describe the device

### play(url, position=0.0)

#### Arguments
* **url (str):**    A URL to video content. It must be accessible by the AirPlay device, and in a format it understands
* **position(float):**   Where to begin playback. 0.0 = start, 1.0 = end.

#### Returns
* **True:**     The request for playback was accepted
* **False:**    There was an error with the request

**Note: A result of True does not mean that playback has actually started!** 
It only means that the AirPlay device accepted the request and will *attempt* playback.


### rate(rate)
Change the playback rate

    >>> ap.rate(0.0)
    True

#### Arguments
* **rate (float):** The playback rate: 0.0 is paused, 1.0 is playing at the normal speed.

#### Returns
* **True:** The playback rate was changed
* **False:** The playback rate request was invald

### stop()
Stop playback

    >>> ap.stop()
    True

#### Returns
* **True:** Playback was stopped

### playback_info()

Retrieve detailed information about the status of video playback

    >>> ap.playback_info()
    {'duration': 60.095, 'playbackLikelyToKeepUp': True, 'readyToPlayMs': 0, 'rate': 1.0, 'playbackBufferEmpty': True, 'playbackBufferFull': False, 'loadedTimeRanges': [{'start': 0.0, 'duration': 60.095}], 'seekableTimeRanges': [{'start': 0.0, 'duration': 60.095}], 'readyToPlay': 1, 'position': 4.144803403}

#### Returns
* **dict:** key/value pairs describing the playback state
* **False:** Nothing is currently being played


### scrub(position=None)

Return the current playback position, optionally seek to a specific position

    >>> ap.scrub()
    {'duration': 60.095001, 'position': 12.465443}

    >>> ap.scrub(30)
    {'duration': 60.095001, 'position': 30.0}



#### Arguments
* **position (float):** If provided, seek to this position

#### Returns
* **dict:** The current position and duration: {'duration': float(seconds), 'position': float(seconds)}

### serve(path)
Start a HTTP server in a new process to serve local content to the AirPlay device

    >>> ap.serve('/tmp/home_movie.mp4')
    'http://192.0.2.114:51058/home_movie.mp4'

#### Arguments
* **path (str):** An absolute path to a file

#### Returns

* **str:** A URL suitable for passing to play()


### events(block=True)

A generator that yields events as they are emitted by the AirPlay device

    >>> for event in ap.events():
    ...   print(event)
    ... 
    {'category': 'video', 'state': 'loading', 'sessionID': 349}
    {'category': 'video', 'state': 'paused', 'sessionID': 349}
    {'category': 'video', 'state': 'playing', 'params': {'duration': 60.095, 'readyToPlay': 1, 'playbackLikelyToKeepUp': True, 'playbackBufferEmpty': True, 'playbackLikelyToKeepUpTime': 0.0, 'position': 0.0, 'playbackBufferFull': False, 'seekableTimeRanges': [{'duration': 60.095, 'start': 0.0}], 'loadedTimeRanges': [{'duration': 60.095, 'start': 0.0}], 'rate': 1.0}, 'sessionID': 349}
    {'category': 'video', 'sessionID': 349, 'type': 'currentItemChanged'}
    {'category': 'video', 'state': 'loading', 'sessionID': 349}
    {'category': 'video', 'reason': 'ended', 'sessionID': 349, 'state': 'stopped'}

#### Arguments

* **block (bool):**     If True, this function will block forever, returning events as they become available.  If False, this function will return if no events are available

#### Yields
* **dict:** key/value pairs describing the event emitted by the AirPlay device


## Need more information?  

The [source for the cli script](airplay/cli.py) is a good example of how to use this package.

The [Unofficial AirPlay Protocol Specification](https://nto.github.io/AirPlay.html#video) documents what data you can send and expect to receive back when using this package.

