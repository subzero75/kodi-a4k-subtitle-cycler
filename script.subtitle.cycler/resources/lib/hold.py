"""Hold the video at its start while add-ons get it ready (audio track, subtitles).

Shared protocol, the same file in every add-on that uses it (Subtitle Cycler, Original
Audio): each add-on registers itself as a holder with a deadline in a home window
property. The first holder pauses the video; when the last holder lets go, or every
deadline has passed, the video plays again. A video the viewer resumed or paused by hand
is left alone.
"""
import json
import time

import xbmc
import xbmcgui

PROP = 'playback.hold'            # {"owner": deadline (unix time)}
PAUSED = 'playback.hold.paused'   # set while the pause is ours


def _home():
    return xbmcgui.Window(10000)


def _holders():
    try:
        got = json.loads(_home().getProperty(PROP) or '{}')
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}


def _save(holders):
    if holders:
        _home().setProperty(PROP, json.dumps(holders))
    else:
        _home().clearProperty(PROP)


def _paused():
    return xbmc.getCondVisibility('Player.Paused')


def start(owner, seconds, message=None):
    """Hold the playing video for up to `seconds` until release(owner)."""
    player = xbmc.Player()
    if not player.isPlayingVideo():
        return
    holders = {k: v for k, v in _holders().items() if v > time.time()}
    holders[owner] = time.time() + seconds
    _save(holders)
    if not _home().getProperty(PAUSED) and not _paused():
        player.pause()
        _home().setProperty(PAUSED, '1')
    if message:
        xbmcgui.Dialog().notification(owner, message, xbmcgui.NOTIFICATION_INFO,
                                      int(seconds * 1000), False)


def release(owner):
    """Let go of the hold; plays the video again when no other add-on still holds it."""
    holders = {k: v for k, v in _holders().items() if v > time.time() and k != owner}
    _save(holders)
    if not holders:
        _resume()


def check():
    """Call every second or so: plays the video again when every deadline has passed."""
    if not _home().getProperty(PAUSED):
        return
    if not any(v > time.time() for v in _holders().values()):
        _save({})
        _resume()


def _resume():
    if not _home().getProperty(PAUSED):
        return
    _home().clearProperty(PAUSED)
    player = xbmc.Player()
    # only undo our own pause: if the viewer already pressed play, leave it playing
    if player.isPlayingVideo() and _paused():
        player.pause()


def stopped():
    """Playback ended: forget everything."""
    _home().clearProperty(PROP)
    _home().clearProperty(PAUSED)
