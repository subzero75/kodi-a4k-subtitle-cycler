import os, threading, time
import xbmc, xbmcgui, xbmcvfs
from resources.lib import common as c
from resources.lib import hold

SKIP_EXT = ('.json', '.db', '.xml', '.log', '.zip', '.rar', '.7z', '.gz', '.py', '.pyc', '.png', '.jpg')
MAX_SIZE = 3 * 1024 * 1024


class Player(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.start = 0
        self.known = {}
        self.prefetch = 0
        self.anime_auto = 0
        self.tag_at = 0
        self.embedded_done = set()
        self.embedded_busy = False
        self.holding = False
        self.auto_started = 0

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
        self.tag_at = time.time() + 3
        xbmcgui.Window(10000).clearProperty('subtitlecycler.tagged')
        self.start = time.time() - 3
        # a4kSubtitles cannot auto-download for Otaku anime (no IMDb id): do it with the button logic
        # a4kSubtitles only auto-downloads when Kodi knows the IMDb id; decided after 15 s
        self.anime_auto = time.time() + 15
        self.auto_started = 0
        self.holding = False
        if c.setting_on('hold_start', False):
            self.anime_auto = time.time() + 4  # no reason to wait while the video is paused
            # Optional: keep the video paused at its start until a subtitle is ready (or the
            # wait runs out); Original Audio can hold it at the same time for the audio track
            self.holding = True
            hold.start('Subtitle Cycler', max(5, c.addon().getSettingInt('hold_max')),
                       c.addon().getLocalizedString(32020))
        self.known = {}
        c.reset_seen()
        # New playback: forget the previous result list so the next press searches again
        try:
            os.remove(c.STATE)
        except OSError:
            pass


    def onPlayBackStopped(self):
        hold.stopped()

    def onPlayBackEnded(self):
        hold.stopped()


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
    gapfix = c.any_fix_on()
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


SIDECAR_EXTS = ('.srt', '.ass', '.ssa')


def sidecar_subs(player):
    """Subtitle files next to a local video ("Movie.mkv" -> "Movie.ar.srt", also in a "Subs"
    folder). Kodi loads those itself, so the service never sees them being written."""
    path = player.getPlayingFile()
    if path.startswith(('http', 'plugin:', 'pvr:', 'upnp:')) or path.lower().endswith('.strm'):
        return []
    folder, name = path.rsplit('/', 1) if '/' in path else ('', path)
    base = os.path.splitext(name)[0].lower()
    out = []
    for d in (folder, folder + '/Subs', folder + '/Subtitles', folder + '/subs'):
        try:
            _, files = xbmcvfs.listdir(d + '/')
        except Exception:
            continue
        out += [d + '/' + f for f in files
                if f.lower().endswith(SIDECAR_EXTS) and f.lower().startswith(base)]
    return out


def fix_sidecar(player):
    """Give a subtitle that sits next to a local video the same gap fix and sync as downloaded
    ones: when Kodi is showing it, load the fixed copy instead."""
    if not c.any_fix_on() or not xbmc.getCondVisibility('VideoPlayer.SubtitlesEnabled'):
        return
    subs = sidecar_subs(player)
    if not subs:
        return
    shown = xbmc.getInfoLabel('VideoPlayer.SubtitlesLanguage')
    want = xbmc.convertLanguage(shown, xbmc.ISO_639_2) if shown else ''
    pick = None
    for sub in subs:
        _, lang = c.display_name(sub)
        if lang and want and xbmc.convertLanguage(lang, xbmc.ISO_639_2) == want:
            pick = sub
            break
    if not pick and len(subs) == 1 and not want:
        pick = subs[0]  # one subtitle without a language in its name, and Kodi shows no language
    if not pick:
        c.log('service: %d subtitle(s) next to the video, none matches the one shown (%s)'
              % (len(subs), shown or 'no language'))
        return
    raw = c.read_bytes(pick)
    if c.digest(raw) in c.seen():
        return
    sub, fixed = c.prepare(pick)
    if fixed and player.isPlayingVideo():
        c.log('service: subtitle next to the video %s -> %s' % (pick, sub))
        tags, notes = list(c.LAST_TAGS), list(c.NOTES)
        if c.load(player, sub, pick):
            c.notify('%s  %s%s' % (' '.join('+' + t for t in tags) or 'in sync',
                                   os.path.basename(pick)[:60],
                                   ''.join('  (%s)' % t for t in notes)), 4000)


def _shown_subtitle():
    """Kodi's current subtitle stream ({'index', 'language', 'name', ...}) or {}."""
    players = c.rpc('Player.GetActivePlayers', {})
    pid = next((p['playerid'] for p in players if p.get('type') == 'video'), 1) \
        if isinstance(players, list) else 1
    return c.rpc('Player.GetProperties', {'playerid': pid, 'properties': ['currentsubtitle']}
                 ).get('currentsubtitle') or {}


def fix_embedded(player, video):
    """Give a text subtitle that comes inside the video (e.g. a fansub release with Arabic
    subtitles) the same gap fix as downloaded ones: read that track from the MKV, close the
    gaps and load the fixed copy in its place. Works for any language Kodi is showing."""
    if not c.gapfix_on() or not xbmc.getCondVisibility('VideoPlayer.SubtitlesEnabled'):
        return
    shown = _shown_subtitle()
    name = shown.get('name') or ''
    if not shown or 'external' in name.lower() or name.lower().endswith(c.SUB_EXTS):
        return  # nothing shown, or already a file (ours or a downloaded one)
    want = xbmc.convertLanguage(shown.get('language') or '', xbmc.ISO_639_2)
    if want not in wanted_languages():
        return  # e.g. the file's default English track while you want another language
    from resources.lib import mkvsubs
    f = c._KodiFile(video)
    try:
        m = mkvsubs.MKV(mkvsubs.Source(f, max_seconds=120))
        tracks = [t for t in m.text_tracks()
                  if xbmc.convertLanguage(t.get('language') or '', xbmc.ISO_639_2) == want]
        if not tracks:
            return  # an image subtitle (PGS) or a language we cannot match
        track = next((t for t in tracks if name and t.get('name') == name), tracks[0])
        events = m.full_events(track, log=c.log)
    except (ValueError, mkvsubs.Limit) as e:
        c.log('embedded subtitle not read: %s' % (str(e) or type(e).__name__))
        return
    finally:
        f.close()
    if not events:
        c.log('embedded subtitle: the cue index does not list its lines, not read')
        return
    data, ext = mkvsubs.to_subtitle_file(track, events)
    # the track as a plain file (named "<video> embedded.<language>"), then the usual fixes
    stem = os.path.splitext(os.path.basename(video.split('?')[0]))[0][:80]
    raw_path = os.path.join(xbmcvfs.translatePath('special://temp/'), '%s embedded.%s%s' % (
        stem, track.get('language') or 'und', ext))
    with open(raw_path, 'wb') as fh:
        fh.write(data)
    if c.seen():
        return  # meanwhile a subtitle was downloaded or chosen: keep that one
    sub, fixed = c.prepare(raw_path)
    if not (fixed and player.isPlayingVideo() and player.getPlayingFile() == video):
        return
    c.log('service: embedded subtitle track %d (%s) -> %s' % (track['number'], track.get('language'), sub))
    tags = list(c.LAST_TAGS)
    if c.load(player, sub, raw_path):
        c.notify('%s  embedded %s subtitle' % (' '.join('+' + t for t in tags),
                                               shown.get('language') or ''), 3000)


def start_embedded_fix(player):
    """fix_embedded in the background: reading the lines of a streamed MKV takes a while."""
    try:
        video = player.getPlayingFile()
    except RuntimeError:
        return
    if not video or video.startswith(('plugin://', 'pvr://')) or video in player.embedded_done:
        return
    player.embedded_done.add(video)
    player.embedded_busy = True

    def work():
        try:
            fix_embedded(player, video)
        except Exception as e:
            c.log('embedded subtitle fix failed: %s' % e, xbmc.LOGWARNING)
        finally:
            player.embedded_busy = False
    threading.Thread(target=work, daemon=True).start()


def wanted_languages():
    """Kodi's subtitle download languages as ISO 639-2 codes ({'ara', 'eng'})."""
    langs = c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'}).get('value') or []
    return {xbmc.convertLanguage(l, xbmc.ISO_639_2) for l in langs} - {''}


def _lang3(code):
    code = code or ''
    return xbmc.convertLanguage(code.split('-')[0], xbmc.ISO_639_2) or code


def showing_wanted():
    """True when Kodi shows a full (not forced) subtitle in one of the subtitle languages."""
    want = {_lang3(l) for l in (c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'})
                                .get('value') or [])}
    shown = _shown_subtitle()
    return bool(xbmc.getCondVisibility('VideoPlayer.SubtitlesEnabled') and shown
                and _lang3(shown.get('language')) in want and not shown.get('isforced'))


def select_wanted_embedded(player):
    """When the video has a full subtitle track in one of the subtitle languages but another
    one is shown (the file's default, a forced track, or one another add-on picked), show
    the wanted one instead. Returns True when it switched."""
    want = [_lang3(l) for l in (c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'})
                                .get('value') or [])]
    if not want:
        return False
    players = c.rpc('Player.GetActivePlayers', {})
    pid = next((p['playerid'] for p in players if p.get('type') == 'video'), 1) \
        if isinstance(players, list) else 1
    props = c.rpc('Player.GetProperties', {'playerid': pid, 'properties': [
        'subtitles', 'currentsubtitle', 'subtitleenabled']})
    shown = props.get('currentsubtitle') or {}
    if props.get('subtitleenabled') and _lang3(shown.get('language')) in want \
            and not shown.get('isforced'):
        return False  # already showing a full subtitle in a wanted language
    tracks = [t for t in props.get('subtitles') or []
              if _lang3(t.get('language')) in want and not t.get('isforced')
              and 'forced' not in (t.get('name') or '').lower()]
    if not tracks:
        return False
    tracks.sort(key=lambda t: want.index(_lang3(t.get('language'))))
    player.setSubtitleStream(tracks[0]['index'])
    player.showSubtitles(True)
    c.log('showing the video\'s own %s subtitle "%s" instead of "%s"' % (
        tracks[0].get('language'), tracks[0].get('name'), shown.get('name') or 'none'))
    return True


def has_wanted_subtitle(player):
    """True when the video has a subtitle track in one of the subtitle download languages."""
    try:
        want = wanted_languages()
        # newer Kodi reports 'ar' / 'ar-SA', older 'ara': compare as ISO 639-2
        have = {xbmc.convertLanguage(l.split('-')[0], xbmc.ISO_639_2) or l
                for l in player.getAvailableSubtitleStreams()}
        return bool(want & have)
    except Exception:
        return False


def auto_load_reason(player):
    """Why the button logic should load a subtitle now, or None."""
    if c.seen():
        return None  # a subtitle was already loaded
    if Player._is_otaku():
        if not c.setting_on('otaku_auto') or has_wanted_subtitle(player):
            return None  # the release brings a subtitle in a wanted language itself
        return 'otaku anime'
    from resources.lib import otaku
    if xbmc.getInfoLabel('VideoPlayer.IMDBNumber').startswith('tt') and not otaku.tagged_by_us():
        return None  # a4kSubtitles takes care of it (it had the IMDb id from the start)
    if not c.setting_on('noid_auto') or has_wanted_subtitle(player):
        return None
    return 'no IMDb id'


def hold_done(player):
    """True when the subtitle work at the start is over, so a held video can play."""
    if c.seen():
        return True  # a subtitle was loaded (downloaded, fixed or chosen)
    if player.tag_at or player.prefetch:
        return False
    if not player.anime_auto and showing_wanted():
        return True  # the video's own subtitle in your language is on (its gap fix may follow)
    if player.embedded_busy:
        return False
    if player.auto_started:
        # the automatic load is running in the button script; done when its lock is gone
        running = xbmcgui.Window(10000).getProperty('subtitlecycler.running')
        return not running and time.time() - player.auto_started > 3
    # no automatic load was needed: give a subtitle add-on's own auto-download a moment
    return not player.anime_auto and time.time() - player.start > 12


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
            if player.tag_at and time.time() > player.tag_at:
                player.tag_at = 0
                try:
                    fix_sidecar(player)
                except Exception as e:
                    c.log('subtitle next to the video failed: %s' % e, xbmc.LOGWARNING)
                start_embedded_fix(player)
                if c.setting_on('noid_auto') or Player._is_otaku():
                    try:
                        from resources.lib import otaku
                        otaku.tag_playing(player)
                    except Exception as e:
                        c.log('tagging the video failed: %s' % e, xbmc.LOGWARNING)
            if player.anime_auto and time.time() > player.anime_auto:
                player.anime_auto = 0
                try:
                    if not c.seen() and select_wanted_embedded(player):
                        # give the track Kodi now shows the gap fix too
                        player.embedded_done.discard(player.getPlayingFile())
                        xbmc.sleep(500)
                        start_embedded_fix(player)
                except Exception as e:
                    c.log('choosing the video\'s own subtitle failed: %s' % e, xbmc.LOGWARNING)
                why = auto_load_reason(player)
                if why:
                    c.log('%s: loading a subtitle automatically' % why)
                    xbmc.executebuiltin('RunScript(script.subtitle.cycler,auto)')
                    player.auto_started = time.time()
            check(player)
            if player.holding and hold_done(player):
                player.holding = False
                hold.release('Subtitle Cycler')
        hold.check()


run()
