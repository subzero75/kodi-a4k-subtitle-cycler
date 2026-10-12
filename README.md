# Subtitle Cycler for Kodi

A Kodi add-on with two independent features:

1. **One-button subtitle cycling** – press a remote button during playback to download and load the next subtitle result from **all enabled subtitle add-ons** (a4kSubtitles, OpenSubtitles, Subdl, …). A notification shows `index/total  source - language`.
2. **No gaps between subtitle lines** (optional) – a background service closes short gaps between lines of every external SRT subtitle, however it was loaded: auto-downloaded at start, picked from Kodi's subtitle window, or loaded with the button. The text no longer blinks between lines.

Tested on Kodi 22 Beta 2 / CoreELEC (Ugoos AM9 Pro).

## Install

1. Download `script.subtitle.cycler-x.y.z.zip` from [Releases](../../releases).
2. In Kodi: **Add-ons → Install from zip file** and pick the zip (enable *Unknown sources* first if Kodi asks).
3. Optional: **Add-ons → My add-ons → Program add-ons → Subtitle Cycler → Configure** and turn on *Close gaps between subtitle lines*.

## Map a remote button

Copy `keymaps/subtitlecycler.xml` to `special://profile/keymaps/` (e.g. `/storage/.kodi/userdata/keymaps/` on CoreELEC/LibreELEC), replace `61634` with your button id, then run `kodi-send --action="ReloadKeymaps"` or restart Kodi.

- **Short press** – next subtitle from all subtitle add-ons.
- **Long press** – opens Kodi's subtitle search window.

**Finding your button id:** in **Keymap Editor**, map the button to any *Fullscreen video* action and save. Open `special://profile/keymaps/gen.xml`, copy the `id` of that key, then delete the temporary mapping (or `gen.xml`) so it does not override this keymap.

Running the add-on from the Programs menu (with no video playing) opens its settings.

## Settings

| Setting | Default | What it does |
|---|---|---|
| Close gaps between subtitle lines | Off | Turns the gap service on or off |
| Close gaps up to (ms) | 1500 | Gaps this short or shorter are closed: a line stays until the next one starts |
| Keep line longer on long gaps (ms) | 2000 | Longer gaps: the line stays this much longer, then the pause stays empty. 0 = off |

## Notes

- The button searches once per video and caches the results; later presses are fast. It skips subtitles already shown in the current video (for example the auto-downloaded one), so you never get the same file twice in a row.
- Gap closing works on text SRT subtitles (UTF-8 and Windows-1256). Embedded image subtitles such as PGS are images, not text, and cannot be changed.
- An auto-downloaded subtitle shows as-is for about a second before it is replaced by the gap-free copy.

## How it works

- **Button:** lists enabled `xbmc.subtitle.module` add-ons through JSON-RPC, calls each with Kodi's standard subtitle interface (`action=search`, then each result's download URL) and loads the file with `xbmc.Player().setSubtitles()`.
- **Service:** every second during playback it looks for new subtitle files written by subtitle add-ons (`addon_data/service.subtitles.*`) or by Kodi (`special://temp`), writes a gap-free copy under a new name (Kodi reuses an already loaded path) and loads it.

## Credits

Written with help from Claude (Anthropic).

## License

MIT
