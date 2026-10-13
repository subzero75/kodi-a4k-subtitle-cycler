import json, os, time
from urllib.parse import quote
import xbmc, xbmcgui
from resources.lib import common as c

PROP_RUNNING = 'subtitlecycler.running'
LOCK_TIMEOUT = 90  # seconds; a stale lock (e.g. after a crash) is ignored after this


def services():
    res = c.rpc('Addons.GetAddons', {'type': 'xbmc.subtitle.module', 'enabled': True, 'properties': ['name']})
    return [(a['addonid'], a.get('name') or a['addonid']) for a in res.get('addons') or []]


def search():
    langs = c.rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'}).get('value') or ['English']
    results = []
    for addon_id, name in services():
        url = 'plugin://%s/?action=search&languages=%s&preferredlanguage=%s' % (
            addon_id, quote(','.join(langs)), quote(langs[0]))
        found = [f for f in c.listdir(url) if f.get('file')]
        c.log('%s: %d results' % (addon_id, len(found)))
        results += [{'label': '%s - %s' % (name, f.get('label', '')), 'file': f['file']} for f in found]
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
    chosen = path = None
    # First pass skips subtitles already shown in this video (e.g. the auto-downloaded one)
    for skip_seen in (True, False):
        already = c.seen() if skip_seen else set()
        for i in order:
            if i in failed or hashes.get(str(i)) in already:
                continue
            got = download(st['results'][i])
            raw = c.read_bytes(got) if got else b''
            if not raw:
                c.log('download failed: %d/%d %s' % (i + 1, n, st['results'][i]['label']))
                failed.add(i)
                continue
            hashes[str(i)] = c.digest(raw)
            if hashes[str(i)] not in already:
                chosen, path = i, got
                break
        if chosen is not None:
            break
    st['failed'] = sorted(failed)
    if chosen is None:
        c.notify('Download failed (daily limit reached?)' if failed else 'No other subtitles')
    else:
        st['index'] = chosen
        sub, fixed = c.prepare(path)
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
