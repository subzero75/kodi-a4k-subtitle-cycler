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
            c.log('otaku: "%s" has no ids, using %s season %s after episode %d' % (
                row.get('mal_title'), row['imdb_id'], season, offset))
        if item.get('movie'):
            return {'imdb_id': row['imdb_id'], 'movie': True, 'title': row.get('mal_title')}
        if not str(season or '').isdigit() or int(season) == 0:
            season, offset = 1, 0  # absolute numbering ("a") or specials: best effort
        return {'imdb_id': row['imdb_id'], 'season': int(season),
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
    show = xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title')
    base = show.split(' - ')[0].strip()
    if base:
        meta = {'imdb_id': '', 'movie': bool(item.get('movie')), 'title': base}
        if not meta['movie']:
            m = SXXEYY.search(os.path.basename(xbmc.Player().getPlayingFile()))
            season, episode = (int(m.group(1)), int(m.group(2))) if m else (
                int(xbmc.getInfoLabel('VideoPlayer.Season') or 1), item['episode'])
            meta.update({'tvshow': base, 'season': season, 'episode': episode})
        if not any(o['movie'] == meta['movie'] and o.get('season') == meta.get('season')
                   and o.get('episode') == meta.get('episode') and o.get('title') == base for o in out):
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
    query = re.sub(r'\s+', '_', title.strip().lower())[:20]
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
        hits = dated or hits
    if not hits:
        c.log('IMDb lookup: no %s called "%s"' % ('movie' if movie else 'series', title))
        return ''
    best = min(hits, key=lambda d: d.get('rank') or 10 ** 9)
    c.log('IMDb lookup: "%s" -> %s (%s %s)' % (title, best['id'], best.get('l'), best.get('y')))
    return best['id']
