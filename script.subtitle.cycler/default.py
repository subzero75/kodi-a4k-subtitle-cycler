import json, os, re, time
from urllib.parse import quote, unquote_plus
import xbmc, xbmcgui
from resources.lib import common as c

PROP_RUNNING = 'subtitlecycler.running'
LOCK_TIMEOUT = 90  # seconds; a stale lock (e.g. after a crash) is ignored after this


def services():
    res = c.rpc('Addons.GetAddons', {'type': 'xbmc.subtitle.module', 'enabled': True, 'properties': ['name']})
    return [(a['addonid'], a.get('name') or a['addonid']) for a in res.get('addons') or []]


EPISODE = re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]?e(\d{1,3})(?!\d)', re.I)
SEASON = re.compile(r'(?<![a-z0-9])(?:s|season[ ._-]?)(\d{1,2})(?![0-9])', re.I)
FILENAME = re.compile(r'"(?:filename|release|name)":\s*"([^"]+)"')


def playing_episode():
    try:
        return int(xbmc.getInfoLabel('VideoPlayer.Season')), int(xbmc.getInfoLabel('VideoPlayer.Episode'))
    except ValueError:
        return None


EPISODE_NUMBER = re.compile(r'(?:\s-\s|\bE|\bEP\s?|\bEpisode\s)(\d{1,4})(?!\d|\s?[-~]\s?\d)', re.I)
RANGE = re.compile(r'\d+\s?[-~]\s?\d+')


def anime_rank(label, hints):
    """0: named after this episode, 1: pack or unknown, 2: another episode."""
    if RANGE.search(label) and not EPISODE_NUMBER.search(RANGE.sub('', label)):
        return 1
    found = {int(n) for n in EPISODE_NUMBER.findall(label)}
    if not found or not hints:
        return 1
    return 0 if found & hints else 2


def otaku_metas():
    """Lookups for subtitle sites when an anime from Otaku is playing (it gives Kodi no IMDb id)."""
    from resources.lib import otaku
    if xbmc.getInfoLabel('VideoPlayer.IMDBNumber').startswith('tt') and not otaku.tagged_by_us():
        return []
    item = otaku.current()
    if not item:
        return []
    metas = otaku.candidates(item)
    for m in metas:
        c.log('otaku: mal %s episode %s -> %s%s' % (
            item['mal_id'], item['episode'], m['imdb_id'] or '"%s"' % m.get('title'),
            '' if m['movie'] else ' S%02dE%02d' % (m['season'], m['episode'])))
    return metas


def name_metas():
    """Lookups from the file name when Kodi knows nothing about the video."""
    if xbmc.getInfoLabel('VideoPlayer.IMDBNumber').startswith('tt'):
        return []
    from resources.lib import otaku
    meta = otaku.from_file_name()
    return [meta] if meta else []


def search():
    langs = c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'}).get('value') or ['English']
    anime = otaku_metas() or name_metas()
    if anime:
        # any numbering one of the lookups uses is the right episode
        episodes = {(m['season'], m['episode']) for m in anime if not m['movie']} or None
    else:
        episodes = {playing_episode()} if playing_episode() else None
    hints = {e[1] for e in episodes or ()}
    results = []
    for addon_id, name in services():
        if anime and addon_id == 'service.subtitles.a4ksubtitles':
            from resources.lib import otaku
            seen_files = set()
            for meta in anime:
                try:
                    found = otaku.a4k_search(meta, langs)
                except Exception as e:
                    c.log('a4k search for anime failed: %s' % e, xbmc.LOGWARNING)
                    found = []
                new = [f for f in found if f['file'] not in seen_files]
                seen_files.update(f['file'] for f in new)
                for f in new:
                    results.append({'label': '%s - %s' % (name, f['label']), 'file': f['file'],
                                    'rank': anime_rank(f['label'], hints)})
                c.log('%s (anime %s): %d results, %d new' % (
                    addon_id, meta['imdb_id'] or meta.get('title'), len(found), len(new)))
            continue
        url = 'plugin://%s/?action=search&languages=%s&preferredlanguage=%s' % (
            addon_id, quote(','.join(langs)), quote(langs[0]))
        found = [f for f in c.listdir(url) if f.get('file')]
        kept = 0
        for f in found:
            text = unquote_plus(f['file'])
            m = FILENAME.search(text)
            label = f.get('label', '')
            if m and m.group(1) not in label:
                label = '%s %s' % (label, m.group(1))
            # Subtitle sites often return the other episodes of the season too: leave those out
            name_text = m.group(1) if m else label
            ep = EPISODE.search(name_text)
            if episodes and ep and (int(ep.group(1)), int(ep.group(2))) not in episodes:
                continue
            se = None if ep else SEASON.search(name_text)
            if episodes and se and int(se.group(1)) not in {e[0] for e in episodes}:
                continue  # season pack of another season
            kept += 1
            results.append({'label': '%s - %s' % (name, label), 'file': f['file']})
        c.log('%s: %d results, %d for other episodes or seasons left out' % (addon_id, len(found), len(found) - kept))
    if anime:
        # Anime release names number episodes their own way ("- 05", "[EP 371]", "1-13"):
        # try the ones named after this episode first, packs next, other episodes last
        results.sort(key=lambda r: r.get('rank', 1))
        c.log('anime results, best first: %s' % ['%s %s' % (r.get('rank', 1), r['label'][-60:]) for r in results])
    return results, hints


def download(item):
    got = [f['file'] for f in c.listdir(item['file']) if f.get('file')]
    return got[0] if got else None


def main():
    p = xbmc.Player()
    if not p.isPlayingVideo():
        c.addon().openSettings()
        return
    home = xbmcgui.Window(10000)
    try:
        running = float(home.getProperty(PROP_RUNNING) or 0)
    except ValueError:
        running = 0
    if time.time() - running < LOCK_TIMEOUT:
        # Another press is still downloading; running both at once wastes downloads and mixes up results
        c.notify('Still loading, please wait...', 1500)
        return
    start = time.time()
    home.setProperty(PROP_RUNNING, str(start))
    c.set_busy(start)
    try:
        cycle(p)
    finally:
        c.set_busy(start, time.time())
        home.clearProperty(PROP_RUNNING)


def load_state():
    try:
        with open(c.STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    with open(c.STATE, 'w') as fh:
        json.dump(st, fh)


def cycle(p):
    cur = p.getPlayingFile()
    st = load_state()
    if st.get('video') != cur or not st.get('results'):
        c.notify('...', 2000)
        res, hints = search()
        if not res:
            c.notify('No subtitles found')
            return
        st = {'video': cur, 'results': res, 'index': -1, 'episodes': sorted(hints)}
    # Remember per result what was downloaded, so results are not downloaded again and again
    # (subtitle sites have daily download limits)
    hashes = st.setdefault('hashes', {})
    failed = set(st.setdefault('failed', []))
    n = len(st['results'])
    order = [(st['index'] + k) % n for k in range(1, n + 1)]
    mismatch = set(st.setdefault('mismatch', []))
    # After "none fits" was shown, later presses show the non-fitting ones anyway
    force = st.get('none_fit', False)
    chosen = path = prepared = None
    checked = 0
    limit_hit = False
    # First pass skips subtitles already shown in this video (e.g. the auto-downloaded one);
    # subtitles whose timing did not fit the video are only tried when forced
    for skip_seen in (True, False):
        already = c.seen() if skip_seen else set()
        for i in order:
            if i in failed or hashes.get(str(i)) in already or (i in mismatch and not force):
                continue
            if checked >= 4 and not force:
                limit_hit = True  # keep each press reasonably quick
                break
            got = download(st['results'][i])
            raw = c.read_bytes(got) if got else b''
            if not raw:
                c.log('download failed: %d/%d %s' % (i + 1, n, st['results'][i]['label']))
                failed.add(i)
                continue
            hashes[str(i)] = c.digest(raw)
            if hashes[str(i)] in already:
                continue
            c.SOURCE[:] = [c.source_name(st['results'][i]['file'])]
            prep = c.prepare(got, st.get('episodes') or ())
            if prep[0] is None:
                # A zip with a text note, a broken download...: never worth showing
                failed.add(i)
                checked += 1
                continue
            if c.NOTES and not force:
                # Timing does not fit this video (other episode or cut): try the next one
                c.log('skipping %d/%d, timing does not fit the video' % (i + 1, n))
                mismatch.add(i)
                checked += 1
                continue
            chosen, path, prepared = i, got, prep
            break
        if chosen is not None or limit_hit:
            break
    st['failed'] = sorted(failed)
    st['mismatch'] = sorted(mismatch)
    if chosen is None and limit_hit:
        c.notify('%d checked, none fits this video yet. Press again to check more' % checked, 4000)
        save_state(st)
        return
    if chosen is None and mismatch and not force:
        st['none_fit'] = True
        c.notify('No subtitle fits this video (%d other episodes/cuts). Press again to show them anyway'
                 % len(mismatch), 5000)
        save_state(st)
        return
    if chosen is None:
        c.notify('Download failed (daily limit reached?)' if failed else 'No other subtitles')
    else:
        st['index'] = chosen
        sub, fixed = prepared
        c.log('loading %d/%d %s -> %s (%d bytes)' % (
            chosen + 1, n, st['results'][chosen]['label'], sub, os.path.getsize(sub)))
        tags = list(c.LAST_TAGS)
        if not c.load(p, sub, path if fixed else None):
            tags = []
        c.notify('%d/%d%s  %s%s' % (chosen + 1, n, ''.join(' +' + t for t in tags),
                                    st['results'][chosen]['label'][:70],
                                    ''.join('  (%s)' % t for t in c.NOTES)), 4000)
    save_state(st)


main()
