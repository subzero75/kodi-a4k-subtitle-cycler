"""Subtitles for anime played from the Otaku add-on.

Otaku takes its anime data from AniList/MAL and does not tell Kodi the IMDb id, so
a4kSubtitles refuses to search ("missing imdb id"). Otaku's own mapping database does know
the IMDb id and the TVDB season of most anime; with those we ask a4kSubtitles to search
through its API, using the season/episode numbering that subtitle sites use.
"""
import json, os, re, sqlite3, sys
from urllib.parse import quote_plus
import xbmc, xbmcgui, xbmcvfs
from resources.lib import common as c

PROP = 'subtitlecycler.otaku'
A4K = 'service.subtitles.a4ksubtitles'
OPEN_FILE = re.compile(rb'VideoPlayer::OpenFile: (plugin://\S+)')
OTAKU_URL = re.compile(r'plugin://plugin\.video\.otaku/(play|play_movie)/(\d+)/(\d*)')
NUMBER = re.compile(r'\d+')


def remember_playback():
    """When playback starts, note the Otaku item being played (taken from kodi.log)."""
    home = xbmcgui.Window(10000)
    home.clearProperty(PROP)
    if not c.setting_on('otaku'):
        return
    try:
        size = os.path.getsize(c.KODI_LOG)
        with open(c.KODI_LOG, 'rb') as fh:
            fh.seek(max(0, size - 4 * 1024 * 1024))
            found = OPEN_FILE.findall(fh.read())
    except OSError:
        return
    if not found:
        return
    m = OTAKU_URL.match(found[-1].decode('utf-8', 'replace'))
    if not m:
        return  # the last plugin item played was not from Otaku
    item = {'movie': m.group(1) == 'play_movie', 'mal_id': int(m.group(2)),
            'episode': int(m.group(3) or 0)}
    home.setProperty(PROP, json.dumps(item))
    c.log('otaku playback: %s' % item)


def current():
    try:
        return json.loads(xbmcgui.Window(10000).getProperty(PROP) or 'null')
    except ValueError:
        return None


def _first_number(text):
    m = NUMBER.search(str(text or ''))
    return int(m.group()) if m else None


def _last_number(text):
    found = NUMBER.findall(str(text or ''))
    return int(found[-1]) if found else None


def lookup(item):
    """{'imdb_id', 'season', 'episode', 'movie'} for subtitle sites, or None."""
    db = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.otaku/mappings.db')
    if not os.path.exists(db):
        c.log('otaku: mappings.db not found')
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute('SELECT * FROM anime WHERE mal_id=?', (item['mal_id'],)).fetchone()
        if not row:
            c.log('otaku: no mapping for mal_id %s' % item['mal_id'])
            return None
        row = dict(row)
        season = row.get('thetvdb_season')
        part = None
        offset = (_first_number(row.get('global_media_episodes')) or 1) - 1
        if not row.get('imdb_id'):
            # New parts often have no ids yet: take them from the earlier parts of the same
            # show ("Title - Part name" -> "Title") and continue their episode numbering.
            base = (row.get('mal_title') or '').split(' - ')[0]
            siblings = [dict(r) for r in con.execute(
                "SELECT * FROM anime WHERE (mal_title=? OR mal_title LIKE ?) AND imdb_id IS NOT NULL "
                "AND imdb_id != '' AND mal_id != ?", (base, base + ' - %', item['mal_id']))]
            siblings = [s for s in siblings if str(s.get('thetvdb_season') or '').isdigit()
                        and int(s['thetvdb_season']) > 0]
            if not siblings:
                c.log('otaku: no IMDb id for "%s"' % row.get('mal_title'))
                return None
            season = max(int(s['thetvdb_season']) for s in siblings)
            same = [s for s in siblings if int(s['thetvdb_season']) == season]
            row['imdb_id'] = same[0]['imdb_id']
            offset = max(_last_number(s.get('global_media_episodes')) or 0 for s in same)
            part = len(same) + 1  # Part 1..3 of Bleach TYBW are season 17, so this is part 4
            c.log('otaku: "%s" has no ids, using %s season %s after episode %d' % (
                row.get('mal_title'), row['imdb_id'], season, offset))
        if item.get('movie'):
            return {'imdb_id': row['imdb_id'], 'movie': True, 'title': row.get('mal_title')}
        if not str(season or '').isdigit() or int(season) == 0:
            season, offset = 1, 0  # absolute numbering ("a") or specials: best effort
        return {'imdb_id': row['imdb_id'], 'season': int(season), 'part': part,
                'episode': offset + item['episode'], 'movie': False, 'title': row.get('mal_title')}
    finally:
        con.close()


SXXEYY = re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)', re.I)


def candidates(item):
    """Ways to look this anime up on subtitle sites, best first.

    1. Otaku's mapping: IMDb id with the TVDB season (e.g. Bleach S17E43).
    2. The show's own English title and the numbering of the release (e.g. "BLEACH:
       Thousand-Year Blood War" S01E03); many sites list newer parts as separate shows.
       a4kSubtitles looks the IMDb id up from the title.
    """
    out = []
    try:
        mapped = lookup(item)
    except Exception as e:
        c.log('otaku lookup failed: %s' % e, xbmc.LOGWARNING)
        mapped = None
    if mapped:
        out.append(mapped)
    tvshow, title, season_label = original_labels()
    show = tvshow or title
    base = show.split(' - ')[0].strip()
    if base:
        # a4kSubtitles gives up without an IMDb id ("missing imdb id!") unless Kodi also knows
        # the year, so look the show's own id up here (e.g. "BLEACH: Thousand-Year Blood War"
        # has its own IMDb entry, separate from the old Bleach series)
        imdb = imdb_from_title(base, movie=bool(item.get('movie')))
        meta = {'imdb_id': imdb, 'movie': bool(item.get('movie')), 'title': base}
        metas = [meta]
        if not meta['movie']:
            m = SXXEYY.search(os.path.basename(xbmc.Player().getPlayingFile()))
            season, episode = (int(m.group(1)), int(m.group(2))) if m else (
                int(season_label or 1), item['episode'])
            meta.update({'tvshow': base, 'season': season, 'episode': episode})
            # Releases often restart at S01 for every part, while subtitle sites list the
            # 4th part as season 4: try that numbering too
            part = mapped.get('part') if mapped else None
            if part and part != season:
                metas.append(dict(meta, season=part, episode=item['episode']))
        for meta in metas:
            if not any(o['movie'] == meta['movie'] and o.get('season') == meta.get('season')
                       and o.get('episode') == meta.get('episode') and o.get('title') == base
                       for o in out):
                out.append(meta)
    return out


def a4k_search(meta, langs):
    """Search a4kSubtitles with our ids; returns [{'label', 'file'}] like a plugin listing."""
    path = xbmcvfs.translatePath('special://home/addons/%s' % A4K)
    if path not in sys.path:
        sys.path.insert(0, path)
    from a4kSubtitles.api import A4kSubtitlesApi
    core = A4kSubtitlesApi().core
    # a4k reads the playing video's details through these info labels; answer the ids
    # ourselves and leave everything else (file name, title) to Kodi. Reporting Kodi 19 makes
    # a4k use the info labels instead of the video info tag. An empty IMDb id makes a4k look
    # it up from the show title.
    labels = {'System.BuildVersionCode': '19.1.0', 'VideoPlayer.IMDBNumber': meta['imdb_id']}
    if not meta['movie']:
        labels.update({'VideoPlayer.Season': str(meta['season']),
                       'VideoPlayer.Episode': str(meta['episode'])})
        if meta.get('tvshow') or not xbmc.getInfoLabel('VideoPlayer.TVShowTitle'):
            labels['VideoPlayer.TVShowTitle'] = meta.get('tvshow') or meta.get('title') or ''
    elif not meta['imdb_id'] and meta.get('title'):
        labels['VideoPlayer.OriginalTitle'] = meta['title']
    real = core.kodi.xbmc.getInfoLabel
    real_notify = core.kodi.notification
    core.kodi.xbmc.getInfoLabel = lambda label: labels[label] if label in labels else real(label)
    core.kodi.notification = lambda *a, **k: None  # no "IMDB ID is not provided" pop-ups
    try:
        params = {'languages': ','.join(langs), 'preferredlanguage': langs[0]}
        results = core.search(core, params) or []
    finally:
        core.kodi.xbmc.getInfoLabel = real
        core.kodi.notification = real_notify
    out = []
    for r in results:
        url = 'plugin://%s/?action=download&service_name=%s&action_args=%s' % (
            A4K, r['service_name'], quote_plus(json.dumps(r['action_args'])))
        out.append({'label': '%s %s' % (r.get('lang') or '', r.get('name') or ''), 'file': url})
    return out


def imdb_from_title(title, year=None, movie=False):
    """IMDb id for a show (or movie) title, from IMDb's own search suggestions (the same
    service a4kSubtitles uses, which it only does when Kodi also knows the year).
    With several matches the best known one wins, e.g. "Lost" -> the 2004 series."""
    from urllib.request import Request, urlopen
    # IMDb's suggestion files are named by letters and digits only ("bleach_thousand_year")
    query = re.sub(r'[^a-z0-9]+', '_', title.strip().lower()).strip('_')[:20]
    if not query:
        return ''
    url = 'https://v2.sg.media-imdb.com/suggestion/%s/%s.json' % (quote_plus(query[0]), quote_plus(query))
    try:
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=10) as r:
            found = json.loads(r.read().decode('utf-8')).get('d') or []
    except Exception as e:
        c.log('IMDb lookup for "%s" failed: %s' % (title, e), xbmc.LOGWARNING)
        return ''
    kinds = ('feature', 'TV movie') if movie else ('TV series', 'TV mini-series')
    want = re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()
    hits = [d for d in found if d.get('q') in kinds and d.get('id', '').startswith('tt')
            and re.sub(r'[^a-z0-9]+', ' ', (d.get('l') or '').lower()).strip() == want]
    if year:
        dated = [d for d in hits if d.get('y') == int(year)]
        if movie and not dated:
            dated = [d for d in hits if abs((d.get('y') or 0) - int(year)) <= 1]
            hits = dated  # a movie of the same name from another year is another movie
        hits = dated or hits
    if not hits:
        c.log('IMDb lookup: no %s called "%s"' % ('movie' if movie else 'series', title))
        return ''
    best = min(hits, key=lambda d: d.get('rank') or 10 ** 9)
    c.log('IMDb lookup: "%s" -> %s (%s %s)' % (title, best['id'], best.get('l'), best.get('y')))
    return best['id']


NAME_SXXEYY = re.compile(r'^(.*?)[\s._\-\[(]+s(\d{1,2})[\s._-]?e(\d{1,3})(?!\d)', re.I)
NAME_NXNN = re.compile(r'^(.*?)[\s._\-\[(]+(\d{1,2})x(\d{2,3})(?!\d)', re.I)
NAME_YEAR = re.compile(r'^(.*?)[\s._\-\[(]+((?:19|20)\d\d)(?!\d)')
SEASON_DIR = re.compile(r'^(?:season|series|staffel|saison|temporada|s)[\s._-]*(\d{1,2})$', re.I)
EPISODE_ONLY = re.compile(r'^(?:e|ep|episode|\#)?[\s._-]*(\d{1,3})(?!\d)', re.I)
VIDEO_EXTS = ('.strm', '.mkv', '.mp4', '.avi', '.ts', '.m2ts', '.mov', '.webm', '.wmv', '.m4v')
JUNK = re.compile(r'\b(?:2160p|1080p|720p|480p|4k|uhd|hdr|web[\s.-]?dl|webrip|bluray|bdrip|remux|x26[45]|h\.?26[45]|hevc)\b.*$', re.I)


def _clean(text):
    """'[Group] The.Matrix.Reloaded_' -> 'The Matrix Reloaded'"""
    text = re.sub(r'^\s*\[[^\]]*\]\s*', '', text)
    text = re.sub(r'[._]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip(' -([')


def _split(path):
    """(folder names from the outside in, file name without extension) of a playing path."""
    path = (path or '').split('?')[0].replace('\\', '/')
    parts = [p for p in path.split('/') if p]
    if not parts:
        return [], ''
    name = parts[-1]
    if name.lower().endswith(VIDEO_EXTS):
        name = os.path.splitext(name)[0]
    from urllib.parse import unquote
    return [unquote(p) for p in parts[:-1]], unquote(name)


def _episode_meta(show, season, episode, year=None):
    show = _clean(show)
    y = NAME_YEAR.search(show)
    if y and not year:
        year = y.group(2)
    show = _clean(NAME_YEAR.sub(r'\1', show)) or show
    if not show:
        return None
    return {'imdb_id': imdb_from_title(show, year), 'movie': False, 'title': show, 'tvshow': show,
            'season': int(season), 'episode': int(episode)}


def from_file_name():
    """{'imdb_id', 'tvshow', 'season', 'episode', ...} or a movie {'imdb_id', 'title', 'movie'}
    from the playing file's name and folders, for videos Kodi knows nothing about (.strm files,
    links played from a file list, local files outside the library). None when nothing fits.

    Episodes: "Lost S01E25 ...", "Lost 1x25 ...", or "Lost/Season 1/25 - Exodus.mkv".
    Movies: "The.Matrix.1999.1080p.mkv" (title and year)."""
    playing = xbmc.Player().getPlayingFile()
    dirs, fname = _split(playing)
    names = [xbmc.getInfoLabel('VideoPlayer.TVShowTitle'), xbmc.getInfoLabel('VideoPlayer.Title'),
             xbmc.getInfoLabel('Player.Filename'), fname]
    names = [os.path.splitext(n)[0] if n.lower().endswith(VIDEO_EXTS) else n for n in names if n]
    meta = None
    for name in names:
        for rx in (NAME_SXXEYY, NAME_NXNN):
            m = rx.search(' ' + name)
            if m and _clean(m.group(1)):
                meta = _episode_meta(m.group(1), m.group(2), m.group(3))
                break
            if m and dirs and not playing.startswith(('http', 'plugin:')):
                # "S01E05.mkv" in a folder named after the show
                show = next((d for d in reversed(dirs) if not SEASON_DIR.match(d.strip())), '')
                meta = _episode_meta(show, m.group(2), m.group(3)) if show else None
                break
        if meta:
            break
    if not meta and len(dirs) >= 2 and not playing.startswith(('http', 'plugin:')):
        # "Lost/Season 1/05 - Exodus.mkv"
        sm = SEASON_DIR.match(dirs[-1].strip())
        em = EPISODE_ONLY.match(fname.strip())
        if sm and em:
            meta = _episode_meta(dirs[-2], sm.group(1), em.group(1))
    if not meta:
        for name in names:
            y = NAME_YEAR.search(name)
            if y and _clean(y.group(1)):
                title = _clean(JUNK.sub('', y.group(1)))
                meta = {'imdb_id': imdb_from_title(title, y.group(2), movie=True), 'movie': True,
                        'title': title, 'year': y.group(2)}
                break
    if meta:
        c.log('no IMDb id: "%s" -> %s' % (fname or names[0], (
            '"%s" (%s) movie' % (meta['title'], meta.get('year')) if meta['movie'] else
            '"%s" S%02dE%02d' % (meta['tvshow'], meta['season'], meta['episode']))))
    return meta


PROP_TAGGED = 'subtitlecycler.tagged'


PROP_ORIGINAL = 'subtitlecycler.original'


def original_labels():
    """[show title, title, season] as the video came, before tag_playing wrote ours over them."""
    if tagged_by_us():
        try:
            saved = json.loads(xbmcgui.Window(10000).getProperty(PROP_ORIGINAL))
            if isinstance(saved, list) and len(saved) == 3:
                return saved
        except ValueError:
            pass
    return [xbmc.getInfoLabel('VideoPlayer.TVShowTitle'), xbmc.getInfoLabel('VideoPlayer.Title'),
            xbmc.getInfoLabel('VideoPlayer.Season')]


def tagged_by_us():
    return bool(xbmcgui.Window(10000).getProperty(PROP_TAGGED))


def _playing_paths(player):
    """Every path Kodi may know the playing item by, its own path (a .strm) first."""
    paths = []
    try:
        players = c.rpc('Player.GetActivePlayers', {})
        pid = next((p['playerid'] for p in players if p.get('type') == 'video'), 1) if isinstance(players, list) else 1
        paths.append(c.rpc('Player.GetItem', {'playerid': pid, 'properties': ['file']}).get('item', {}).get('file'))
    except Exception:
        pass
    paths.append(xbmc.getInfoLabel('Player.FilenameAndPath'))
    try:
        paths.append(player.getPlayingFile())
    except Exception:
        pass
    out = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


def tag_playing(player):
    """Give the playing video the IMDb id, show, season and episode we found, so that Kodi's
    own subtitle search window, a4kSubtitles and any other subtitle add-on can search for it
    too, not only the button. Returns True when the video was updated."""
    home = xbmcgui.Window(10000)
    home.clearProperty(PROP_TAGGED)
    original = original_labels()
    home.setProperty(PROP_ORIGINAL, json.dumps(original))
    if xbmc.getInfoLabel('VideoPlayer.IMDBNumber').startswith('tt'):
        return False
    meta = None
    item = current()
    if item:
        found = candidates(item)
        meta = next((m for m in found if m.get('imdb_id')), None)
        if not meta and found and not found[0]['movie']:
            meta = dict(found[0], imdb_id=imdb_from_title(found[0].get('tvshow') or found[0]['title']))
    else:
        meta = from_file_name()
    if not meta or not meta.get('imdb_id') or not player.isPlayingVideo():
        return False
    try:
        updated = False
        # Kodi only updates the playing item when the ListItem path is the item's own path.
        # For a .strm that is the .strm file, not the stream it points to (getPlayingFile),
        # so try every path Kodi knows for it and check that the IMDb id really arrived.
        for path in _playing_paths(player):
            li = xbmcgui.ListItem(path=path)
            tag = li.getVideoInfoTag()
            tag.setIMDBNumber(meta['imdb_id'])
            tag.setUniqueIDs({'imdb': meta['imdb_id']}, 'imdb')
            title = xbmc.getInfoLabel('VideoPlayer.Title') or meta.get('title') or ''
            if title:
                tag.setTitle(title)
            if meta['movie']:
                tag.setMediaType('movie')
            else:
                tag.setMediaType('episode')
                # keep the show's own (English) title; ours may be Otaku's romaji MAL title
                tag.setTvShowTitle(original[0] or meta.get('tvshow') or meta.get('title') or '')
                tag.setSeason(int(meta['season']))
                tag.setEpisode(int(meta['episode']))
            player.updateInfoTag(li)
            for _ in range(15):
                if xbmc.getInfoLabel('VideoPlayer.IMDBNumber') == meta['imdb_id']:
                    updated = True
                    break
                xbmc.sleep(100)
            if updated:
                break
            c.log('playing video kept no IMDb id with path %s' % path)
    except Exception as e:
        c.log('could not update the playing video: %s' % e, xbmc.LOGWARNING)
        return False
    if not updated:
        c.log('could not give the playing video its IMDb id, Kodi ignored every path', xbmc.LOGWARNING)
        return False
    home.setProperty(PROP_TAGGED, meta['imdb_id'])
    c.log('playing video now has %s%s, so every subtitle search can find it' % (
        meta['imdb_id'], '' if meta['movie'] else ' S%02dE%02d' % (meta['season'], meta['episode'])))
    return True
