import json
from urllib.parse import quote
import xbmc, xbmcgui, xbmcvfs

ADDON = 'service.subtitles.a4ksubtitles'
STATE = xbmcvfs.translatePath('special://temp/a4knext.json')

def rpc(method, params):
    q = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    return json.loads(xbmc.executeJSONRPC(json.dumps(q))).get('result') or {}

def notify(msg, ms=3000):
    xbmcgui.Dialog().notification('a4kSubtitles', msg, xbmcgui.NOTIFICATION_INFO, ms)

def listdir(path):
    return rpc('Files.GetDirectory', {'directory': path, 'media': 'files'}).get('files') or []

def search():
    langs = rpc('Settings.GetSettingValue', {'setting': 'subtitles.languages'}).get('value') or ['Arabic']
    url = 'plugin://%s/?action=search&languages=%s&preferredlanguage=%s' % (
        ADDON, quote(','.join(langs)), quote(langs[0]))
    return [{'label': f.get('label', ''), 'file': f['file']} for f in listdir(url) if f.get('file')]

def main():
    p = xbmc.Player()
    if not p.isPlayingVideo():
        return
    cur = p.getPlayingFile()
    try:
        with open(STATE) as fh:
            st = json.load(fh)
    except Exception:
        st = {}
    if st.get('video') != cur or not st.get('results'):
        notify('...', 2000)
        res = search()
        if not res:
            notify('No subtitles found')
            return
        st = {'video': cur, 'results': res, 'index': 0}
    n = len(st['results'])
    for _ in range(n):
        st['index'] = (st['index'] + 1) % n
        item = st['results'][st['index']]
        got = [f['file'] for f in listdir(item['file']) if f.get('file')]
        if got:
            p.setSubtitles(got[0])
            p.showSubtitles(True)
            notify('%d/%d  %s' % (st['index'] + 1, n, item['label'][:60]), 4000)
            break
    else:
        notify('Download failed')
    with open(STATE, 'w') as fh:
        json.dump(st, fh)

main()
