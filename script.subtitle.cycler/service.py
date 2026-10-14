import os, time
import xbmc, xbmcvfs
from resources.lib import common as c

SKIP_EXT = ('.json', '.db', '.xml', '.log', '.zip', '.rar', '.7z', '.gz', '.py', '.pyc', '.png', '.jpg')
MAX_SIZE = 3 * 1024 * 1024


class Player(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.start = 0
        self.known = {}
        self.prefetch = 0
        self.anime_auto = 0

    @staticmethod
    def _is_otaku():
        try:
            from resources.lib import otaku
            return bool(otaku.current())
        except Exception:
            return False

    def onAVStarted(self):
        try:
            from resources.lib import otaku
            otaku.remember_playback()
        except Exception as e:
            c.log('otaku check failed: %s' % e, xbmc.LOGWARNING)
        self.prefetch = time.time() + 5
        self.start = time.time() - 3
        # a4kSubtitles cannot auto-download for Otaku anime (no IMDb id): do it with the button logic
        self.anime_auto = time.time() + 15 if self._is_otaku() and c.setting_on('otaku_auto') else 0
        self.known = {}
        c.reset_seen()
        # New playback: forget the previous result list so the next press searches again
        try:
            os.remove(c.STATE)
        except OSError:
            pass


def candidates():
    """Subtitle files that subtitle add-ons or Kodi itself may have just written."""
    base = xbmcvfs.translatePath('special://profile/addon_data/')
    try:
        dirs = [os.path.join(base, d) for d in os.listdir(base) if d.startswith('service.subtitles.')]
    except OSError:
        dirs = []
    for d in dirs + [os.path.join(c.TEMP, 'subtitles')]:
        for root, _, files in os.walk(d):
            for name in files:
                if not name.lower().endswith(SKIP_EXT):
                    yield os.path.join(root, name)
    try:
        for name in os.listdir(c.TEMP):
            if name.lower().endswith('.srt') and not name.startswith(c.OUT_PREFIX):
                yield os.path.join(c.TEMP, name)
    except OSError:
        pass


def in_busy(m, busy):
    if not busy:
        return False
    a, b = busy
    return m >= a - 0.5 and (b == 0 or m <= b + 2)


def skip(player, path, m, why):
    """Remember a file we leave alone and say why once, so a missed subtitle can be explained."""
    player.known[path] = m
    c.log('service: not touching %s (%s)' % (os.path.basename(path), why))


def check(player):
    gapfix = c.gapfix_on() or c.sync_on()
    now = time.time()
    busy = c.busy_window()
    if busy and (busy[0] < player.start - 60 or (busy[1] == 0 and now - busy[0] > 90)):
        busy = None  # left over from an earlier video or a press that never finished
    for path in candidates():
        try:
            st = os.stat(path)
        except OSError:
            continue
        m = st.st_mtime
        if player.known.get(path) == m or now - m < 0.8:
            continue
        if m < player.start:
            player.known[path] = m  # from an earlier video
            continue
        if st.st_size > MAX_SIZE:
            skip(player, path, m, 'too big')
            continue
        if busy and busy[1] == 0 and m >= busy[0] - 0.5:
            continue  # script still downloading; look again once it is done
        player.known[path] = m
        if in_busy(m, busy):
            c.log('service: %s comes from the button, leaving it to the button' % os.path.basename(path))
            continue  # written by the script's own downloads
        try:
            raw = c.read_bytes(path)
            text = c.decode(raw[:4096])
            if not text or not (c.TS.search(text) or c.is_ass(c.decode(raw) or '')):
                skip(player, path, m, 'not a text subtitle')
                continue
            if c.digest(raw) in c.seen():
                skip(player, path, m, 'already loaded')
                continue
            if not gapfix:
                c.mark_seen(c.digest(raw))  # so the button skips it
                continue
            sub, fixed = c.prepare(path)
            if fixed and player.isPlayingVideo():
                c.log('service loading %s -> %s' % (path, sub))
                tags = list(c.LAST_TAGS)
                notes = list(c.NOTES)
                if c.load(player, sub, path):
                    # show what was done to a subtitle picked by hand, like the button does
                    c.notify('%s  %s%s' % (' '.join('+' + t for t in tags) or 'in sync',
                                           os.path.basename(path)[:60],
                                           ''.join('  (%s)' % t for t in notes)), 4000)
        except Exception as e:
            c.log('failed on %s: %s' % (path, e), xbmc.LOGWARNING)


def run():
    monitor = xbmc.Monitor()
    player = Player()
    c.log('service started')
    while not monitor.waitForAbort(1):
        if player.isPlayingVideo() and player.start:
            if player.prefetch and time.time() > player.prefetch:
                player.prefetch = 0
                if c.sync_on():
                    try:
                        # read the embedded subtitle timings now, so the first button press is quick
                        c.reference_starts(player.getPlayingFile(), quiet=True)
                    except Exception as e:
                        c.log('prefetch failed: %s' % e, xbmc.LOGWARNING)
            if player.anime_auto and time.time() > player.anime_auto:
                player.anime_auto = 0
                if not c.seen():  # no subtitle was loaded yet
                    c.log('otaku anime: loading a subtitle automatically')
                    xbmc.executebuiltin('RunScript(script.subtitle.cycler)')
            check(player)


run()
