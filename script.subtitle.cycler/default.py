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


def otaku_meta():
    """Ids for subtitle sites when an anime from Otaku is playing (it gives Kodi no IMDb id)."""
    if xbmc.getInfoLabel('VideoPlayer.IMDBNumber').startswith('tt'):
        return None
    from resources.lib import otaku
    item = otaku.current()
    if not item:
        return None
    try:
        meta = otaku.lookup(item)
    except Exception as e:
        c.log('otaku lookup failed: %s' % e, xbmc.LOGWARNING)
        return None
    if meta:
        c.log('otaku: mal %s episode %s -> %s%s' % (
            item['mal_id'], item['episode'], meta['imdb_id'],
            '' if meta['movie'] else ' S%02dE%02d' % (meta['season'], meta['episode'])))
    return meta


def search():
    langs = c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'}).get('value') or ['English']
    anime = otaku_meta()
    if anime:
        episode = None if anime['movie'] else (anime['season'], anime['episode'])
    else:
        episode = playing_episode()
    results = []
    for addon_id, name in services():
        if anime and addon_id == 'service.subtitles.a4ksubtitles':
            from resources.lib import otaku
            try:
                found = otaku.a4k_search(anime, langs)
            except Exception as e:
                c.log('a4k search for anime failed: %s' % e, xbmc.LOGWARNING)
                found = []
            for f in found:
                results.append({'label': '%s - %s' % (name, f['label']), 'file': f['file']})
            c.log('%s (anime ids): %d results' % (addon_id, len(found)))
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
            if episode and ep and (int(ep.group(1)), int(ep.group(2))) != episode:
                continue
            se = None if ep else SEASON.search(name_text)
            if episode and se and int(se.group(1)) != episode[0]:
                continue  # season pack of another season
            kept += 1
            results.append({'label': '%s - %s' % (name, label), 'file': f['file']})
        c.log('%s: %d results, %d for other episodes or seasons left out' % (addon_id, len(found), len(found) - kept))
    return results


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
        res = search()
        if not res:
            c.notify('No subtitles found')
            return
        st = {'video': cur, 'results': res, 'index': -1}
    # Remember per result what was downloaded, so results are not downloaded again and again
    # (subtitle sites have daily download limits)
    hashes = st.setdefault('hashes', {})
    failed = set(st.setdefault('failed', []))
    n = len(st['results'])
    order = [(st['index'] + k) % n for k in range(1, n + 1)]
    mismatch = set(st.setdefault('mismatch', []))
    chosen = path = prepared = None
    fallback = None  # a subtitle whose timing did not fit, used if nothing better turns up
    extra = 0
    # First pass skips subtitles already shown in this video (e.g. the auto-downloaded one)
    # and those whose timing did not fit the video
    for skip_seen in (True, False):
        already = c.seen() if skip_seen else set()
        for i in order:
            if i in failed or hashes.get(str(i)) in already or (skip_seen and i in mismatch):
                continue
            got = download(st['results'][i])
            raw = c.read_bytes(got) if got else b''
            if not raw:
                c.log('download failed: %d/%d %s' % (i + 1, n, st['results'][i]['label']))
                failed.add(i)
                continue
            hashes[str(i)] = c.digest(raw)
            if hashes[str(i)] in already:
                continue
            prep = c.prepare(got)
            if c.NOTES and extra < 3:
                # Timing does not fit this video (other episode or cut): try the next one
                c.log('skipping %d/%d, timing does not fit the video' % (i + 1, n))
                mismatch.add(i)
                if fallback is None:
                    fallback = (i, got, prep, list(c.LAST_TAGS), list(c.NOTES))
                extra += 1
                continue
            chosen, path, prepared = i, got, prep
            break
        if chosen is not None:
            break
    if chosen is None and fallback:
        chosen, path, prepared, c.LAST_TAGS[:], c.NOTES[:] = fallback
    st['failed'] = sorted(failed)
    st['mismatch'] = sorted(mismatch)
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
