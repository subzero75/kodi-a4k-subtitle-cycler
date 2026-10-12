# a4kSubtitles Cycler for Kodi

One remote button to jump through a4kSubtitles results while a video is playing.

- **Short press** – searches a4kSubtitles (first press only), downloads the **next** result and switches to it. A notification shows `index/total  name`. Wraps around at the end.
- **Long press** – opens Kodi's subtitle search window so you can pick from the full a4kSubtitles list.

The first press starts from result #2, because a4kSubtitles usually auto-loads result #1 already.
Results are cached per video, so later presses are fast. A new video triggers a fresh search.

## Requirements

- Kodi 20 or newer (tested on Kodi 22 Beta 2, CoreELEC, Ugoos AM9 Pro)
- [a4kSubtitles](https://github.com/a4k-openproject/a4kSubtitles) installed
- Subtitle download languages set in *Settings → Player → Language → Languages to download subtitles for* (falls back to Arabic if empty)

## Install

1. Copy `a4knext.py` to your Kodi profile folder (`special://profile/`, e.g. `/storage/.kodi/userdata/` on CoreELEC/LibreELEC).
2. Copy `keymaps/a4knext.xml` to `special://profile/keymaps/`.
3. Replace `61634` with your own button id (see below). If your profile folder is not `/storage/.kodi/userdata/`, change the script path in the keymap too.
4. Reload keymaps: `kodi-send --action="ReloadKeymaps"` or restart Kodi.

### Finding your button id

Easiest way: in **Keymap Editor**, map your button to any Fullscreen video action and save. Then open `special://profile/keymaps/gen.xml` and copy the `id` of that key. Remove the temporary mapping from `gen.xml` afterwards so it does not override this keymap.

## How it works

The script calls the a4kSubtitles plugin through Kodi's standard subtitle-service interface
(`action=search`, then the download URL of each result) using JSON-RPC `Files.GetDirectory`,
and loads the downloaded file with `xbmc.Player().setSubtitles()`. State is kept in
`special://temp/a4knext.json`.

## License

MIT
