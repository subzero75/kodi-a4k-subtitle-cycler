import hashlib, json, os, re, time, uuid
import xbmc, xbmcaddon, xbmcgui, xbmcvfs

ADDON_ID = 'script.subtitle.cycler'
TEMP = xbmcvfs.translatePath('special://temp/')
OUT_PREFIX = 'sc_'
# Our copies live in the add-on's own data folder, not in special://temp: Kodi treats files there
# whose name contains "subtitle" as its own temporary subtitles, and loading them failed.
OUT_DIR = xbmcvfs.translatePath('special://profile/addon_data/%s/subs/' % 'script.subtitle.cycler')
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


def parse_srt(text):
    """List of [start_ms, end_ms, body] sorted by start, or [] if the text is not SRT."""
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip()):
        lines = block.split('\n')
        for i, line in enumerate(lines):
            m = TS.search(line)
            if m:
                g = m.groups()
                cues.append([_ms(*g[:4]), _ms(*g[4:]), '\n'.join(lines[i + 1:])])
                break
    cues.sort(key=lambda c: c[0])
    return cues


def format_srt(cues):
    return ''.join('%d\n%s --> %s\n%s\n\n' % (n, _fmt(a), _fmt(b), body)
                   for n, (a, b, body) in enumerate(cues, 1))


def gap_close(cues, max_gap, extend):
    """Close short gaps between lines and keep lines a little longer over long gaps."""
    for cur, nxt in zip(cues, cues[1:]):
        gap = nxt[0] - cur[1]
        if 0 < gap <= max_gap:
            cur[1] = nxt[0]
        elif gap > max_gap:
            cur[1] += min(extend, gap)
    return cues


def close_gaps(text, max_gap, extend):
    """Return SRT text with short gaps closed and long gaps shortened, or None if not SRT."""
    cues = parse_srt(text)
    return format_srt(gap_close(cues, max_gap, extend)) if cues else None


# ---------------------------------------------------------------- sync to embedded subtitles

REF_DIR = xbmcvfs.translatePath('special://profile/addon_data/%s/ref/' % ADDON_ID)


class _KodiFile:
    def __init__(self, url):
        self.f = xbmcvfs.File(url)

    def seek(self, pos):
        self.f.seek(pos, 0)

    def read(self, n):
        return bytes(self.f.readBytes(n))

    def close(self):
        self.f.close()


def reference_starts(video, quiet=False):
    """Start times (ms) of an embedded text subtitle track of the playing MKV, cached per video."""
    if not video or video.startswith(('plugin://', 'pvr://')):
        return None
    key = digest(video.encode('utf-8'))
    cache = os.path.join(REF_DIR, key + '.json')
    try:
        with open(cache) as fh:
            return json.load(fh).get('starts')
    except (OSError, ValueError):
        pass
    from resources.lib import mkvsubs
    if not quiet:
        notify('Reading embedded subtitles for sync...', 2000)
    t0 = time.time()
    starts = None
    note = ''
    f = _KodiFile(video)
    try:
        m = mkvsubs.MKV(mkvsubs.Source(f))
        track = mkvsubs.pick_track(m.text_tracks())
        if not track:
            note = 'no embedded text subtitle track'
        else:
            starts = [s for s, e in m.events(track, log=log)]
            note = 'track %d (%s %s)' % (track['number'], track.get('language'), track.get('codec'))
    except ValueError:
        note = 'not an MKV file, sync works with MKV only'
    except Exception as e:  # network error, ...
        note = 'cannot read embedded subtitles: %s' % e
    finally:
        f.close()
    log('reference for sync: %s, %d lines in %.1fs' % (note, len(starts or []), time.time() - t0))
    try:
        os.makedirs(REF_DIR, exist_ok=True)
        with open(cache, 'w') as fh:
            json.dump({'video': video, 'starts': starts, 'note': note}, fh)
    except OSError:
        pass
    return starts


def sync_cues(cues):
    """Shift cues to match the video's embedded subtitles. Returns (cues, tag or None)."""
    try:
        video = xbmc.Player().getPlayingFile()
    except RuntimeError:
        return cues, None
    ref = reference_starts(video)
    if not ref:
        return cues, None
    from resources.lib import subsync
    res = subsync.find_shift(ref, [c[0] for c in cues])
    if not res:
        log('sync: no confident match (%d reference lines)' % len(ref))
        return cues, 'no sync'
    a, b = res['a'], res['b']
    log('sync: x%.5f %+.0f ms (%d/%d lines matched)' % (a, b, res['matched'], res['total']))
    if abs(a - 1) < 1e-4 and abs(b) < 150:
        return cues, 'in sync'  # nothing to change
    tag = 'sync %+.1fs' % (b / 1000.0)
    if abs(a - 1) >= 1e-4:
        tag += ' x%.3f' % a
    return subsync.apply(cues, a, b), tag


def gapfix_on():
    return addon().getSettingBool('gapfix')


def sync_on():
    try:
        return addon().getSettingBool('sync')
    except Exception:
        return False


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
    os.makedirs(OUT_DIR, exist_ok=True)
    old = sorted((os.path.join(OUT_DIR, n) for n in os.listdir(OUT_DIR) if n.startswith(OUT_PREFIX)),
                 key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    for name in old[:-5]:
        try:
            os.remove(name)
        except OSError:
            pass
    # Kodi reuses an already loaded subtitle with the same path, so every file gets a new name
    dst = os.path.join(OUT_DIR, '%s%s%s' % (OUT_PREFIX, uuid.uuid4().hex[:12], ext))
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
    for _ in range(5):
        xbmc.sleep(100)
        if _parser_failed(mark):
            fallback = original
            log('Kodi could not parse %s, loading original %s' % (sub, original),
                xbmc.LOGWARNING)
            player.setSubtitles(fallback)
            player.showSubtitles(True)
            return False
    return True


LAST_TAGS = []
NOTES = []


def prepare(path):
    """Register the subtitle as seen and return (fresh copy to load, changed).
    What was changed ('gapfix', 'sync +1.2s') is left in LAST_TAGS."""
    del LAST_TAGS[:]
    del NOTES[:]
    raw = read_bytes(path)
    mark_seen(digest(raw))
    text = decode(raw[:4096]) or ''
    is_srt = bool(TS.search(text))
    gap, sync = gapfix_on(), sync_on()
    if is_srt and (gap or sync):
        cues = parse_srt(decode(raw))
        if cues:
            if sync:
                cues, tag = sync_cues(cues)
                if tag == 'no sync':
                    NOTES.append('timing differs from the video: other episode or cut?')
                elif tag:
                    LAST_TAGS.append(tag)
            if gap:
                a = addon()
                gap_close(cues, a.getSettingInt('max_gap'), a.getSettingInt('extend'))
                LAST_TAGS.append('gapfix')
        if LAST_TAGS:
            # Plain UTF-8 without BOM and with CRLF line ends, like the files Kodi loads fine
            dst = _write_unique(format_srt(cues).replace('\n', '\r\n').encode('utf-8'), '.srt')
            log('%s: %s -> %s' % (', '.join(LAST_TAGS), path, dst))
            return dst, True
    ext = '.srt' if is_srt else (os.path.splitext(path)[1] or '.srt')
    dst = _write_unique(raw, ext)
    log('copied: %s -> %s' % (path, dst))
    return dst, False
