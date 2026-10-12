import hashlib, json, os, re, time
import xbmc, xbmcaddon, xbmcgui, xbmcvfs

ADDON_ID = 'script.subtitle.cycler'
TEMP = xbmcvfs.translatePath('special://temp/')
OUT_PREFIX = 'subtitlecycler_'
PROP_SEEN = 'subtitlecycler.seen'
TS = re.compile(r'(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})')


def addon():
    return xbmcaddon.Addon(ADDON_ID)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[subtitlecycler] %s' % msg, level)


def notify(msg, ms=3000):
    xbmcgui.Dialog().notification('Subtitle Cycler', msg, xbmcgui.NOTIFICATION_INFO, ms)


def rpc(method, params):
    q = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    return json.loads(xbmc.executeJSONRPC(json.dumps(q))).get('result') or {}


def listdir(path):
    return rpc('Files.GetDirectory', {'directory': path, 'media': 'files'}).get('files') or []


def read_bytes(path):
    f = xbmcvfs.File(path)
    try:
        return bytes(f.readBytes())
    finally:
        f.close()


def decode(raw):
    for enc in ('utf-8-sig', 'cp1256', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass


def digest(raw):
    return hashlib.sha1(raw).hexdigest()


# Hashes of subtitles already loaded during the current playback, shared by script and service.
def seen():
    try:
        return set(json.loads(xbmcgui.Window(10000).getProperty(PROP_SEEN) or '[]'))
    except ValueError:
        return set()


def mark_seen(h):
    s = seen()
    s.add(h)
    xbmcgui.Window(10000).setProperty(PROP_SEEN, json.dumps(sorted(s)))


def reset_seen():
    xbmcgui.Window(10000).clearProperty(PROP_SEEN)


def _ms(h, m, s, f):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(f.ljust(3, '0'))


def _fmt(ms):
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return '%02d:%02d:%02d,%03d' % (h, m, s, ms)


def close_gaps(text, max_gap, extend):
    """Return SRT text with short gaps closed and long gaps shortened, or None if not SRT."""
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip()):
        lines = block.split('\n')
        for i, line in enumerate(lines):
            m = TS.search(line)
            if m:
                g = m.groups()
                cues.append([_ms(*g[:4]), _ms(*g[4:]), '\n'.join(lines[i + 1:])])
                break
    if not cues:
        return None
    cues.sort(key=lambda c: c[0])
    for cur, nxt in zip(cues, cues[1:]):
        gap = nxt[0] - cur[1]
        if 0 < gap <= max_gap:
            cur[1] = nxt[0]
        elif gap > max_gap:
            cur[1] += min(extend, gap)
    return ''.join('%d\n%s --> %s\n%s\n\n' % (n, _fmt(a), _fmt(b), body)
                   for n, (a, b, body) in enumerate(cues, 1))


def prepare(path):
    """Register the subtitle as seen and return (path to load, gaps_closed)."""
    raw = read_bytes(path)
    mark_seen(digest(raw))
    a = addon()
    if not a.getSettingBool('gapfix'):
        return path, False
    out = close_gaps(decode(raw), a.getSettingInt('max_gap'), a.getSettingInt('extend'))
    if out is None:
        return path, False
    for name in os.listdir(TEMP):
        if name.startswith(OUT_PREFIX) and name.endswith('.srt'):
            try:
                os.remove(os.path.join(TEMP, name))
            except OSError:
                pass
    # Kodi reuses an already loaded subtitle with the same path, so every file gets a new name
    dst = os.path.join(TEMP, '%s%d.srt' % (OUT_PREFIX, int(time.time() * 1000)))
    with open(dst, 'w', encoding='utf-8-sig') as fh:
        fh.write(out)
    log('gaps closed: %s -> %s' % (path, dst))
    return dst, True
