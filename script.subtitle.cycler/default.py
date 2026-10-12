import json
from urllib.parse import quote
import xbmc, xbmcvfs
from resources.lib import common as c

STATE = xbmcvfs.translatePath('special://temp/subtitlecycler.json')


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
    cur = p.getPlayingFile()
    try:
        with open(STATE) as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    if st.get('video') != cur or not st.get('results'):
        c.notify('...', 2000)
        res = search()
        if not res:
            c.notify('No subtitles found')
            return
        st = {'video': cur, 'results': res, 'index': -1}
    n = len(st['results'])
    order = [(st['index'] + k) % n for k in range(1, n + 1)]
    cache = {}
    chosen = None
    # First pass skips subtitles already shown in this video (e.g. the auto-downloaded one)
    for skip_seen in (True, False):
        already = c.seen() if skip_seen else set()
        for i in order:
            if i not in cache:
                path = download(st['results'][i])
                cache[i] = (path, c.digest(c.read_bytes(path)) if path else None)
            path, h = cache[i]
            if path and h not in already:
                chosen = i
                break
        if chosen is not None:
            break
    if chosen is None:
        c.notify('Download failed')
    else:
        st['index'] = chosen
        sub, fixed = c.prepare(cache[chosen][0])
        p.setSubtitles(sub)
        p.showSubtitles(True)
        c.notify('%d/%d%s  %s' % (chosen + 1, n, ' +gapfix' if fixed else '',
                                  st['results'][chosen]['label'][:70]), 4000)
    with open(STATE, 'w') as fh:
        json.dump(st, fh)


main()
