# Subtitle Cycler for Kodi

A Kodi add-on with four independent features:

1. **One-button subtitle cycling** – press a remote button during playback to download and load the next subtitle result from **all enabled subtitle add-ons** (a4kSubtitles, OpenSubtitles, Subdl, …). A notification shows `index/total  source - language`.
2. **No gaps between subtitle lines** (optional) – a background service closes short gaps between lines of every external SRT subtitle, however it was loaded: auto-downloaded at start, picked from Kodi's subtitle window, or loaded with the button. The text no longer blinks between lines.
3. **Sync to the embedded subtitles** (optional, **experimental**) – when the video is an MKV with a text subtitle track (in any language), external SRT subtitles are shifted, and stretched for 23.976/24/25 fps mix-ups, so their timing matches that track. Handy when the only well-timed track is in another language than the one you want.
4. **Subtitles for videos Kodi has no IMDb id for** (optional, **experimental**) – a4kSubtitles only searches, and only auto-downloads, when Kodi knows the video's IMDb id. Some players do not pass it on, and `.strm` files or links played from a file list have none. The add-on then finds the id itself and loads the first subtitle that fits:
   - **Anime from [Otaku](https://github.com/Goldenfreddy0703/Otaku):** ids from Otaku's own database, plus a search by the show's title. Tested only with *Bleach: Thousand-Year Blood War* so far.
   - **Other videos:** the show name, season and episode from the file name (`Lost S01E25 ...`), and the IMDb id from IMDb's search.

Tested on Kodi 22 Beta 2 / CoreELEC (Ugoos AM9 Pro). Sync has also been reported working on Kodi 21.2 (Google TV Streamer) by a user.

## Install

1. Download `script.subtitle.cycler-x.y.z.zip` from [Releases](../../releases).
2. In Kodi: **Add-ons → Install from zip file** and pick the zip (enable *Unknown sources* first if Kodi asks).
3. Optional: **Add-ons → My add-ons → Program add-ons → Subtitle Cycler → Configure** and turn on *Close gaps between subtitle lines*.

### Checking a download

The zip in each release is built by GitHub from the release's tag (see `.github/workflows/release.yml`), not uploaded by hand, and GitHub signs where it came from. To check a zip you downloaded:

```
gh attestation verify script.subtitle.cycler-x.y.z.zip -R subzero75/kodi-subtitle-cycler
```

It only passes for a zip that GitHub built from this repository's code. Each release also has a `.sha256` file with the zip's checksum.

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
| Sync timing to the video's embedded subtitles | Off | Experimental. Shift external SRTs to match the MKV's text subtitle track. The notification shows `+sync -2.4s` when the timing was changed, `+in sync` when it already matched, and *timing differs from the video* when the subtitle belongs to another episode or cut. Turn it off for the quickest cycling |
| Find subtitles for anime played from Otaku | On | Experimental. a4kSubtitles search with the ids from Otaku's database. Only does something while an Otaku episode plays |
| Load a subtitle automatically when an Otaku episode starts | On | About 15 seconds after the episode starts, the first subtitle that fits the video is loaded |
| Load a subtitle automatically when the video has no IMDb id | On | a4kSubtitles only auto-downloads when Kodi knows the IMDb id, and some players (e.g. some TMDb Helper players) do not pass it on. Then the first subtitle that fits is loaded about 15 seconds after the start, unless the video already has a subtitle track in one of your subtitle languages |

## Notes

- The button searches once per video and caches the results; later presses are fast. It skips subtitles already shown in the current video (for example the auto-downloaded one), so you never get the same file twice in a row.
- Gap closing and sync work on text SRT and ASS/SSA subtitles (UTF-8 and Windows-1256); ASS styling is kept. Embedded image subtitles such as PGS are images, not text, and cannot be changed.
- Sync needs an MKV with a **text** subtitle track (SRT/ASS); image tracks (PGS, VobSub) and MP4 files are skipped. Only the start times are compared, so the languages do not have to match. Only a small part of the video is read (the cue index plus a sample of subtitle blocks, usually a few MB), so it also works for streamed debrid/Usenet files; the result is cached per video. When the timings do not line up clearly, the subtitle is left unchanged.
- For TV episodes, results named for another episode (`S01E05`) or another season's pack (`S01`, `Season.01`) are left out of the list.
- With sync on, a subtitle whose timing does not fit the video is skipped and the next one is tried (up to 4 per press). When none fits, the notification says so; press again to show them anyway. Subtitles picked by hand from Kodi's subtitle dialog get the same gap fix and sync, with the same notification.
- Sync is new and has only been tested on a few TV series (one device, MKV files with an English text subtitle track). Please open an issue with your `kodi.log` if it shifts a subtitle the wrong way.
- **Anime from Otaku:** Otaku gives Kodi no IMDb id, so a4kSubtitles cannot search. For Otaku playback the button looks up the IMDb id and TVDB season in Otaku's own mapping database (newer parts continue the numbering of earlier ones) and searches a4kSubtitles through its API. A subtitle is also loaded automatically about 15 seconds after an Otaku episode starts. It searches both with that id and with the show's own title, and tries releases named after the playing episode first, then season packs. Long shows with absolute numbering (e.g. One Piece) may find fewer results. Tested only with Bleach: Thousand-Year Blood War so far.
- When Kodi knows nothing about the video (no IMDb id, e.g. a `.strm` file), the show name, season and episode are taken from the file name (`Lost S01E25 ...`) and a4kSubtitles searches by title.
- Zip files from subtitle sites are opened and the subtitle for the playing episode is taken out; files that are not subtitles (a text note, a web page, a rar archive) are skipped and never shown.
- ASS subtitles without `PlayResX`/`PlayResY` get the canvas size their fonts and margins were made for. Without it Kodi assumes 384x288, and such files show one word per line.
- Subtitles loaded by the add-on keep their real name, language and site in Kodi's subtitle list (e.g. `Arabic - Lost - s01e22 - Born to Run - OpenSubtitles`).
- An auto-downloaded subtitle shows as-is for about a second before it is replaced by the gap-free copy.

## How it works

- **Button:** lists enabled `xbmc.subtitle.module` add-ons through JSON-RPC, calls each with Kodi's standard subtitle interface (`action=search`, then each result's download URL) and loads the file with `xbmc.Player().setSubtitles()`.
- **Service:** every second during playback it looks for new subtitle files written by subtitle add-ons (`addon_data/service.subtitles.*`) or by Kodi (`special://temp`), writes a gap-free copy under a new name (Kodi reuses an already loaded path) and loads it. Copies are kept in `addon_data/script.subtitle.cycler/subs/`.
- **Sync:** a small Matroska reader (`mkvsubs.py`) reads the start times of the embedded text track through Kodi's VFS, and `subsync.py` finds the offset and frame-rate ratio that best lines up the external subtitle's start times with them (a histogram of time differences, then a least-squares refinement). A match is only used when it is clearly better than any other offset.

## Credits

Written with help from Claude (Anthropic).

## License

MIT
