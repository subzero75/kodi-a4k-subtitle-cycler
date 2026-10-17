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
PAUSED = 'playback.hold.paused'   # time of our last pause, set while the hold is ours
REPAUSED = 'playback.hold.repaused'  # how often Kodi started the video by itself
SETTLE = 4  # seconds after our pause in which a start is Kodi's own, not the viewer's
MAX_REPAUSE = 5
FILE = 'playback.hold.file'       # the video the hold is for
MESSAGE = 'playback.hold.message' # [heading, text, last shown] of the notice while held
SHOW_MS = 3000  # the notice is shown in short pieces, so it is gone soon after the hold ends


def _log(msg):
    xbmc.log('[playback hold] %s' % msg, xbmc.LOGINFO)


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


def _caching():
    return xbmc.getCondVisibility('Player.Caching')


def _set_paused(paused):
    """Pause or play explicitly. xbmc.Player().pause() toggles, so two add-ons pausing at the
    same moment would pause and play again; Player.PlayPause with play=false/true does not."""
    q = {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetActivePlayers'}
    players = json.loads(xbmc.executeJSONRPC(json.dumps(q))).get('result') or []
    pid = next((p['playerid'] for p in players if p.get('type') == 'video'), 1)
    q = {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.PlayPause',
         'params': {'playerid': pid, 'play': not paused}}
    xbmc.executeJSONRPC(json.dumps(q))


def start(owner, seconds, message=None):
    """Hold the playing video for up to `seconds` until release(owner)."""
    player = xbmc.Player()
    if not player.isPlayingVideo():
        _log('%s: no video playing, not held' % owner)
        return
    try:
        video = player.getPlayingFile()
    except RuntimeError:
        video = ''
    if _home().getProperty(FILE) != video:
        # a new video (the next episode can start without a stop in between): start afresh
        stopped()
        _home().setProperty(FILE, video)
    holders = {k: v for k, v in _holders().items() if v > time.time()}
    holders[owner] = time.time() + seconds
    _save(holders)
    if not _home().getProperty(PAUSED):
        # also when Kodi still shows the video as paused (filling its start-up buffer):
        # check() then pauses it as soon as Kodi starts it
        _home().setProperty(PAUSED, str(time.time()))
        _home().setProperty(REPAUSED, '0')
        if _paused():
            _log('%s: holding (Kodi is still loading the video)' % owner)
        else:
            _set_paused(True)
            _log('%s: paused for up to %d s' % (owner, seconds))
    else:
        _log('%s: joined the hold' % owner)
    if message:
        _home().setProperty(MESSAGE, json.dumps([owner, message, 0]))
        _notice()


def _notice():
    """Show the notice again shortly before the last one fades, while the video is held."""
    try:
        heading, text, shown = json.loads(_home().getProperty(MESSAGE) or 'null')
    except (TypeError, ValueError):
        return
    if time.time() - shown < SHOW_MS / 1000.0 - 1:
        return
    xbmcgui.Dialog().notification(heading, text, xbmcgui.NOTIFICATION_INFO, SHOW_MS, False)
    _home().setProperty(MESSAGE, json.dumps([heading, text, time.time()]))


def release(owner):
    """Let go of the hold; plays the video again when no other add-on still holds it."""
    holders = {k: v for k, v in _holders().items() if v > time.time() and k != owner}
    _save(holders)
    if not holders:
        _resume('%s is ready' % owner)


def check():
    """Call every second or so: plays the video again when every deadline has passed, pauses
    it again when Kodi starts it by itself, and keeps the notice up while it is held."""
    if not _home().getProperty(PAUSED):
        return
    if not any(v > time.time() for v in _holders().values()):
        _save({})
        _resume('waited long enough')
        return
    try:
        since = time.time() - float(_home().getProperty(PAUSED))
    except ValueError:
        since = SETTLE
    if _paused() or _caching():
        if _caching():
            # Kodi is still filling its buffer and will start the video when it is full
            _home().setProperty(PAUSED, str(time.time()))
        _notice()
        return
    if not xbmc.Player().isPlayingVideo():
        return
    try:
        count = int(_home().getProperty(REPAUSED) or 0)
    except ValueError:
        count = 0
    if since < SETTLE and count < MAX_REPAUSE:
        # Kodi starts the video by itself once its start-up buffer is full, and again after
        # it switches a subtitle or audio track: pause once more
        _home().setProperty(REPAUSED, str(count + 1))
        _home().setProperty(PAUSED, str(time.time()))
        _set_paused(True)
        _log('Kodi started the video by itself, paused again')
        _notice()
        return
    # playing a while after our pause: the viewer pressed play. The hold is over, and a
    # pause the viewer makes later is theirs, not ours to undo
    _log('the viewer started the video, hold ended')
    _save({})
    _home().clearProperty(PAUSED)
    _home().clearProperty(REPAUSED)
    _home().clearProperty(MESSAGE)


def _resume(why):
    _home().clearProperty(MESSAGE)
    if not _home().getProperty(PAUSED):
        return
    _home().clearProperty(PAUSED)
    _home().clearProperty(REPAUSED)
    player = xbmc.Player()
    # only undo our own pause: if the viewer already pressed play, leave it playing
    if player.isPlayingVideo() and _paused():
        _set_paused(False)
        _log('playing again (%s)' % why)


def stopped():
    """Playback ended: forget everything."""
    _home().clearProperty(PROP)
    _home().clearProperty(PAUSED)
    _home().clearProperty(REPAUSED)
    _home().clearProperty(MESSAGE)
    _home().clearProperty(FILE)
