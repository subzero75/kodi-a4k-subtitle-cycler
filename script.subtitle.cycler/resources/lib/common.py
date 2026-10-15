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


def _utf16(raw):
    """'utf-16-le'/'utf-16-be' when the bytes look like UTF-16, else None."""
    if raw[:2] == b'\xff\xfe':
        return 'utf-16-le'
    if raw[:2] == b'\xfe\xff':
        return 'utf-16-be'
    sample = raw[:4000]
    if len(sample) >= 40:
        if sample[1::2].count(0) > len(sample) // 4:
            return 'utf-16-le'
        if sample[0::2].count(0) > len(sample) // 4:
            return 'utf-16-be'
    return None


def decode(raw):
    enc16 = _utf16(raw)
    if enc16:
        text = raw[:len(raw) - len(raw) % 2].decode(enc16, 'replace')
        return text[1:] if text.startswith('\ufeff') else text
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


# ---------------------------------------------------------------- ASS/SSA subtitles

ASS_TIME = re.compile(r'^(\d+):(\d\d):(\d\d)[.:](\d{1,3})$')


def is_ass(text):
    return '[Events]' in text and 'Dialogue:' in text


def _ass_ms(t):
    m = ASS_TIME.match(t.strip())
    if not m:
        return None
    h, mi, se, frac = m.groups()
    return ((int(h) * 60 + int(mi)) * 60 + int(se)) * 1000 + int(frac.ljust(3, '0')[:3])


def _ass_fmt(ms):
    cs = int(round(ms / 10.0))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return '%d:%02d:%02d.%02d' % (h, m, sec, cs)


def parse_ass(text):
    """(lines, cues): cues are [start_ms, end_ms, line index] of the Dialogue lines, sorted."""
    lines = text.replace('\r\n', '\n').split('\n')
    fields = ['layer', 'start', 'end', 'style', 'name', 'marginl', 'marginr', 'marginv', 'effect', 'text']
    in_events = False
    cues = []
    for i, line in enumerate(lines):
        st = line.strip()
        if st.startswith('['):
            in_events = st.lower() == '[events]'
        elif in_events and st.lower().startswith('format:'):
            fields = [f.strip().lower() for f in st.split(':', 1)[1].split(',')]
        elif in_events and st.startswith('Dialogue:'):
            parts = line.split(':', 1)[1].split(',', len(fields) - 1)
            try:
                a = _ass_ms(parts[fields.index('start')])
                b = _ass_ms(parts[fields.index('end')])
            except (ValueError, IndexError):
                continue
            if a is not None and b is not None:
                cues.append([a, b, i])
    cues.sort(key=lambda c: c[0])
    return lines, cues, fields


POS = re.compile(r'\\(?:pos|move|org)\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)')


def fix_playres(lines):
    """Add PlayResX/PlayResY when the file has none; returns True when it did.

    Without them libass assumes a 384x288 canvas. Files made for 1080p video then get
    their margins and sizes read on that tiny canvas: text squeezed into a narrow column,
    one word per line."""
    info = style_fmt = None
    sizes, margins, coords = [], [], []
    section = ''
    for i, line in enumerate(lines):
        st = line.strip()
        low = st.lower()
        if st.startswith('['):
            section = low
            if low == '[script info]':
                info = i
        elif section == '[script info]' and low.startswith(('playresx', 'playresy')):
            return False
        elif section in ('[v4+ styles]', '[v4 styles]') and low.startswith('format:'):
            style_fmt = [f.strip().lower() for f in st.split(':', 1)[1].split(',')]
        elif section in ('[v4+ styles]', '[v4 styles]') and low.startswith('style:') and style_fmt:
            parts = st.split(':', 1)[1].split(',')
            try:
                sizes.append(float(parts[style_fmt.index('fontsize')]))
            except (ValueError, IndexError):
                pass
            try:
                lr = float(parts[style_fmt.index('marginl')]) + float(parts[style_fmt.index('marginr')])
                margins.append(lr)
            except (ValueError, IndexError):
                pass
        elif section == '[events]' and st.startswith('Dialogue:'):
            coords.extend(float(x) for m in POS.findall(st) for x in m)
    size = sorted(sizes)[len(sizes) // 2] if sizes else 0  # the usual dialogue size
    override = kodi_overrides_ass_styles()
    canvases = [(384, 288), (640, 360), (1280, 720), (1920, 1080)]
    if override:
        # Kodi's own style (font, margins) is made for a 1080p canvas
        w, h = 1920, 1080
    else:
        # the smallest canvas on which the file's sizes make sense: text of normal size,
        # left+right margins leaving most of the width for the text, \pos inside the picture
        need = max([m * 2.5 for m in margins] + coords[0::2] + [0])
        need_h = max([size * 12.5] + coords[1::2] + [0])  # dialogue text is ~8% of the height
        w, h = next(((cw, ch) for cw, ch in canvases if cw >= need and ch >= need_h), canvases[-1])
    if info is None:
        lines[0:0] = ['[Script Info]', 'ScriptType: v4.00+', '']
        info = 0
    lines.insert(info + 1, 'PlayResY: %d' % h)
    lines.insert(info + 1, 'PlayResX: %d' % w)
    log('subtitle has no PlayResX/PlayResY, added %dx%d (font size %g, side margins up to %g, '
        'Kodi overrides styles: %s)' % (w, h, size, max(margins + [0]), override))
    return True


def kodi_overrides_ass_styles():
    """True when Kodi draws ASS subtitles with its own style (Settings > Player > Language)."""
    try:
        value = rpc('Settings.GetSettingValue', {'setting': 'subtitles.overridestyles'}).get('value')
        log('Kodi setting subtitles.overridestyles = %r' % (value,))
        if isinstance(value, bool):
            return value
        return int(value or 0) >= 2  # 0 never, 1 positions, 2 styles, 3 styles and positions
    except Exception:
        return False


POSITIONED = re.compile(r'\\(?:pos|move)\(|\\an\d|\\[kK][fo]?\d')


def ass_dialogue(lines, cues, fields):
    """Which lines are ordinary dialogue: the most used style, not placed or karaoke-timed.

    Only those get the gap fix. Songs (lyrics and their translation shown together), signs and
    other typesetting have their own timing; keeping them longer stacks them on screen."""
    try:
        si, ti = fields.index('style'), fields.index('text')
    except ValueError:
        return None
    style = {}
    for _, _, i in cues:
        parts = lines[i].split(':', 1)[1].split(',', len(fields) - 1)
        if len(parts) > ti:
            style[i] = (parts[si].strip().lower(), bool(POSITIONED.search(parts[ti])))
    counts = {}
    for name, placed in style.values():
        if not placed:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    total = sum(counts.values())
    # the dialogue styles: the most used one, and any other carrying a good share of the lines
    main = {n for n, k in counts.items() if k >= max(counts.values()) * 0.3 or k >= total * 0.2}
    log('ASS gap fix only for styles %s (%d of %d lines)' % (
        sorted(main), sum(counts[n] for n in main), len(cues)))
    return lambda i: i in style and style[i][0] in main and not style[i][1]


def format_ass(lines, cues, fields):
    """Write the new times back into the Dialogue lines; lines of dropped cues are removed."""
    keep = {}
    for a, b, i in cues:
        keep[i] = (a, b)
    si, ei = fields.index('start'), fields.index('end')
    out = []
    dialogue = {i for i, l in enumerate(lines) if l.strip().startswith('Dialogue:')}
    for i, line in enumerate(lines):
        if i in keep:
            head, rest = line.split(':', 1)
            parts = rest.split(',', len(fields) - 1)
            lead = parts[si][:len(parts[si]) - len(parts[si].lstrip())]
            parts[si] = lead + _ass_fmt(keep[i][0])
            parts[ei] = _ass_fmt(keep[i][1])
            out.append(head + ':' + ','.join(parts))
        elif i in dialogue and _ass_line_times(line, fields):
            continue  # a parsed line that was dropped (shifted before 0:00)
        else:
            out.append(line)
    return '\r\n'.join(out)


def _ass_line_times(line, fields):
    try:
        parts = line.split(':', 1)[1].split(',', len(fields) - 1)
        return _ass_ms(parts[fields.index('start')]) is not None
    except (ValueError, IndexError):
        return False


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
    if len(ref) < 30:
        log('sync: only %d reference lines, too few to judge' % len(ref))
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


def any_fix_on():
    return gapfix_on() or sync_on()


def gapfix_on():
    return addon().getSettingBool('gapfix')


def setting_on(key, default=True):
    try:
        return addon().getSettingBool(key)
    except Exception:
        return default


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


BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _language_token(token):
    """'ara' / 'ar' / 'arabic' -> the token itself when it names a language, else None."""
    t = token.strip().lower()
    if not t.isalpha() or len(t) < 2:
        return None
    for fmt in (xbmc.ISO_639_1, xbmc.ISO_639_2):
        try:
            if xbmc.convertLanguage(t, fmt):
                return t
        except Exception:
            pass
    return None


# Where the subtitle came from (e.g. "OpenSubtitles"); set by the button before prepare()
SOURCE = []
SITE_NAMES = {'opensubtitles': 'OpenSubtitles', 'subdl': 'Subdl', 'subsource': 'SubSource',
              'podnadpisi': 'Podnapisi', 'podnapisi': 'Podnapisi', 'addic7ed': 'Addic7ed',
              'bsplayer': 'BSPlayer', 'subscene': 'Subscene', 'yifysubtitles': 'YIFY',
              'gestdown': 'Gestdown', 'napiprojekt': 'NapiProjekt', 'subtitlecat': 'SubtitleCat'}


def source_name(url):
    """Nice site name for a subtitle add-on download URL: a4kSubtitles' own site
    (service_name=...), else the add-on's name (service.subtitles.opensubtitles_com ->
    OpenSubtitles)."""
    m = re.search(r'[?&]service_name=([A-Za-z0-9_]+)', url or '')
    if m:
        key = m.group(1).lower()
        return SITE_NAMES.get(key, key.capitalize())
    m = re.match(r'plugin://service\.subtitles\.([A-Za-z0-9_]+)', url or '')
    if m:
        key = re.sub(r'_(com|org|net)$', '', m.group(1).lower())
        return SITE_NAMES.get(key, key.capitalize())
    return ''


def display_name(src):
    """(name, language) for the copy of src, so Kodi's subtitle list shows e.g.
    'Arabic - Lost - s01e22 - Born to Run' instead of a random file name."""
    base = os.path.basename(src or '')
    stem = base
    while True:
        root, ext = os.path.splitext(stem)
        if ext.lower() in SUB_EXTS or ext.lower() in ('.zip',):
            stem = root
        else:
            break
    lang = None
    parts = stem.split('.')
    if len(parts) > 1 and _language_token(parts[-1]):
        lang = parts[-1].lower()
        parts = parts[:-1]
    # Kodi splits the file name at dots (language, "forced", ...): keep them out of the name
    name = BAD_NAME_CHARS.sub(' ', ' '.join(parts)).strip()
    name = re.sub(r'\s+', ' ', name)[:80].strip() or 'cycler'
    return name, lang


def _write_unique(data, ext, src=None):
    # Keep the few newest copies: Kodi may still be opening one of them. Kodi itself deletes
    # them all when playback stops.
    os.makedirs(OUT_DIR, exist_ok=True)
    old = sorted((os.path.join(OUT_DIR, n) for n in os.listdir(OUT_DIR)),
                 key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    for name in old[:-5]:
        try:
            os.remove(name)
        except OSError:
            pass
    # Kodi reuses an already loaded subtitle with the same path, so every file gets a new name;
    # the short code keeps names unique without hiding the real name in Kodi's list
    name, lang = display_name(src)
    if SOURCE and SOURCE[0]:
        name = '%s - %s' % (name, SOURCE[0])
    code = uuid.uuid4().hex[:4]
    dst = os.path.join(OUT_DIR, '%s [%s]%s%s' % (name, code, '.' + lang if lang else '', ext))
    with open(dst, 'wb') as fh:
        fh.write(data)
    return dst


def copy_original(path):
    raw = read_bytes(path)
    is_srt = bool(TS.search(decode(raw[:4096]) or ''))
    return _write_unique(raw, '.srt' if is_srt else (os.path.splitext(path)[1] or '.srt'), path)


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


def _fix(cues, gap, sync, only=None):
    """Sync and/or close gaps in place (cues: [start, end, payload]); returns the cues.
    only(payload): which cues get the gap fix (default all)."""
    if sync:
        new, tag = sync_cues(cues)
        cues[:] = new
        if tag == 'no sync':
            NOTES.append('timing differs from the video: other episode or cut?')
        elif tag:
            LAST_TAGS.append(tag)
    if gap:
        a = addon()
        gap_close([q for q in cues if only is None or only(q[2])],
                  a.getSettingInt('max_gap'), a.getSettingInt('extend'))
        LAST_TAGS.append('gapfix')
    return cues


SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt', '.sub', '.smi')
NUMBERS = re.compile(r'\d+')


def unpack(raw, episodes=()):
    """Subtitle files inside a zip archive, the ones named after the episode first."""
    import io, zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = [n for n in z.namelist() if os.path.splitext(n)[1].lower() in SUB_EXTS]
        hint = {int(e) for e in episodes or ()}
        names.sort(key=lambda n: not hint & {int(x) for x in NUMBERS.findall(os.path.basename(n))})
        return [(n, z.read(n)) for n in names[:12]]
    except Exception as e:
        log('could not open archive: %s' % e)
        return []


def unusable(why, path):
    NOTES.append(why)
    log('%s: %s' % (why, path))
    return None, False


def prepare(path, episodes=()):
    """Register the subtitle as seen and return (fresh copy to load, changed); (None, False)
    when it is not a subtitle Kodi can show (NOTES says why).
    What was changed ('gapfix', 'sync +1.2s') is left in LAST_TAGS."""
    raw = read_bytes(path)
    mark_seen(digest(raw))
    if raw[:4] == b'PK\x03\x04':
        # Some sites hand out zip archives (season packs, or a text file with a link)
        entries = unpack(raw, episodes)
        if not entries:
            del LAST_TAGS[:]
            del NOTES[:]
            return unusable('archive without subtitles', path)
        for name, data in entries:
            got = _prepare_raw(data, '%s/%s' % (path, name))
            if got[0] and not NOTES:
                break
        return got
    if raw[:4] == b'Rar!' or raw[:6] == b"7z\xbc\xaf'\x1c":
        del LAST_TAGS[:]
        del NOTES[:]
        return unusable('archive type not supported', path)
    return _prepare_raw(raw, path)


def _prepare_raw(raw, path):
    del LAST_TAGS[:]
    del NOTES[:]
    text = decode(raw[:4096]) or ''
    if not raw.strip() or ('\x00' in text and not _utf16(raw)):
        return unusable('not a subtitle file', path)
    is_srt = bool(TS.search(text))
    gap, sync = gapfix_on(), sync_on()
    if is_srt and (gap or sync):
        cues = parse_srt(decode(raw))
        if cues:
            cues = _fix(cues, gap, sync)
        if LAST_TAGS:
            # Plain UTF-8 without BOM and with CRLF line ends, like the files Kodi loads fine
            dst = _write_unique(format_srt(cues).replace('\n', '\r\n').encode('utf-8'), '.srt', path)
            log('%s: %s -> %s' % (', '.join(LAST_TAGS), path, dst))
            return dst, True
    elif not is_srt and is_ass(decode(raw) or ''):
        lines, cues, fields = parse_ass(decode(raw))
        before = len(lines)
        res = fix_playres(lines)
        if res:
            # line indexes of the cues moved by the inserted lines (all above the events)
            cues = [[a, b, i + len(lines) - before] for a, b, i in cues]
        if cues or res:
            if cues and (gap or sync):
                dialogue = ass_dialogue(lines, cues, fields)
                _fix(cues, gap, sync, dialogue)
            if LAST_TAGS or res:
                ext = os.path.splitext(path)[1].lower()
                dst = _write_unique(format_ass(lines, cues, fields).encode('utf-8'),
                                    ext if ext in ('.ass', '.ssa') else '.ass', path)
                log('%s: %s -> %s' % (', '.join(LAST_TAGS), path, dst))
                return dst, True
    ext = os.path.splitext(path)[1].lower()
    if not is_srt and ext not in SUB_EXTS and not is_ass(text) and not re.search(r'^WEBVTT', text):
        # No subtitle timings anywhere: a text note, a web page, a broken download...
        log('format not recognised; first bytes: %r' % raw[:48])
        return unusable('not a subtitle file', path)
    ext = '.srt' if is_srt else (ext if ext in SUB_EXTS else '.srt')
    dst = _write_unique(raw, ext, path)
    log('copied: %s -> %s' % (path, dst))
    if (gap or sync) and not is_srt:
        log('not changed, format not recognised; first bytes: %r' % raw[:48])
    return dst, False
