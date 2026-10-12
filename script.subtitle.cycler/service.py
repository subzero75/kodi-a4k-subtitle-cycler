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

    def onAVStarted(self):
        self.start = time.time() - 3
        self.known = {}
        c.reset_seen()


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


def check(player):
    now = time.time()
    for path in candidates():
        try:
            st = os.stat(path)
        except OSError:
            continue
        m = st.st_mtime
        if m < player.start or player.known.get(path) == m or now - m < 0.8 or st.st_size > MAX_SIZE:
            continue
        player.known[path] = m
        try:
            raw = c.read_bytes(path)
            text = c.decode(raw[:4096])
            if not text or not c.TS.search(text) or c.digest(raw) in c.seen():
                continue
            sub, fixed = c.prepare(path)
            if fixed and player.isPlayingVideo():
                player.setSubtitles(sub)
                player.showSubtitles(True)
        except Exception as e:
            c.log('failed on %s: %s' % (path, e), xbmc.LOGWARNING)


def run():
    monitor = xbmc.Monitor()
    player = Player()
    c.log('service started')
    while not monitor.waitForAbort(1):
        if player.isPlayingVideo() and player.start:
            check(player)


run()
