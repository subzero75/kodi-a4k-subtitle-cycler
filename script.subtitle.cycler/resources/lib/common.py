import hashlib, json, os, re, time, uuid
import xbmc, xbmcaddon, xbmcgui, xbmcvfs

ADDON_ID = 'script.subtitle.cycler'
TEMP = xbmcvfs.translatePath('special://temp/')
OUT_PREFIX = 'subtitlecycler_'
PROP_SEEN = 'subtitlecycler.seen'
STATE = os.path.join(TEMP, 'subtitlecycler.json')
KODI_LOG = os.path.join(xbmcvfs.translatePath('special://logpath/'), 'kodi.log')
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


def gapfix_on():
    return addon().getSettingBool('gapfix')


# The script sets this while it is downloading, so the service leaves those files alone.
PROP_BUSY = 'subtitlecycler.busy'


def set_busy(start, end=0.0):
    xbmcgui.Window(10000).setProperty(PROP_BUSY, '%f %f' % (start, end))


def busy_window():
    try:
        a, b = xbmcgui.Window(10000).getProperty(PROP_BUSY).split()
        return float(a), float(b)
    except ValueError:
        return None


def _write_unique(data, ext):
    # Keep the few newest copies: Kodi may still be opening one of them. Kodi itself deletes
    # them all when playback stops.
    old = sorted((os.path.join(TEMP, n) for n in os.listdir(TEMP) if n.startswith(OUT_PREFIX)),
                 key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    for name in old[:-3]:
        try:
            os.remove(name)
        except OSError:
            pass
    # Kodi reuses an already loaded subtitle with the same path, so every file gets a new name
    dst = os.path.join(TEMP, '%s%s%s' % (OUT_PREFIX, uuid.uuid4().hex[:12], ext))
    with open(dst, 'wb') as fh:
        fh.write(data)
    return dst


def copy_original(path):
    raw = read_bytes(path)
    is_srt = bool(TS.search(decode(raw[:4096]) or ''))
    return _write_unique(raw, '.srt' if is_srt else (os.path.splitext(path)[1] or '.srt'))


def _log_size():
    try:
        return os.path.getsize(KODI_LOG)
    except OSError:
        return 0


def _parser_failed(since):
    try:
        with open(KODI_LOG, 'rb') as fh:
            fh.seek(since)
            return b'Unable to create subtitle parser' in fh.read()
    except OSError:
        return False


def load(player, sub, original=None):
    """Load a subtitle; if Kodi cannot parse it, fall back to a copy of the original file.
    Returns True when the given subtitle was used, False when the fallback was."""
    mark = _log_size()
    player.setSubtitles(sub)
    player.showSubtitles(True)
    if not original:
        return True
    for _ in range(10):
        xbmc.sleep(100)
        if _parser_failed(mark):
            fallback = copy_original(original)
            log('Kodi could not parse %s, loading original %s as %s' % (sub, original, fallback),
                xbmc.LOGWARNING)
            player.setSubtitles(fallback)
            player.showSubtitles(True)
            return False
    return True


def prepare(path):
    """Register the subtitle as seen and return (fresh copy to load, gaps_closed)."""
    raw = read_bytes(path)
    mark_seen(digest(raw))
    text = decode(raw[:4096]) or ''
    is_srt = bool(TS.search(text))
    if is_srt and gapfix_on():
        a = addon()
        out = close_gaps(decode(raw), a.getSettingInt('max_gap'), a.getSettingInt('extend'))
        if out is not None:
            # Plain UTF-8 without BOM and with CRLF line ends, like the files Kodi loads fine
            dst = _write_unique(out.replace('\n', '\r\n').encode('utf-8'), '.srt')
            log('gaps closed: %s -> %s' % (path, dst))
            return dst, True
    ext = '.srt' if is_srt else (os.path.splitext(path)[1] or '.srt')
    dst = _write_unique(raw, ext)
    log('copied: %s -> %s' % (path, dst))
    return dst, False
