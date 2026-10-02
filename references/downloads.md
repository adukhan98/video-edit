# Downloading media with yt-dlp

Every piece of media the edit pulls from the internet — the user's own uploads, licensed stock,
reference videos, b-roll, music beds — goes through `helpers/fetch.py`, which wraps yt-dlp, caches by
URL + section, and logs source and licence to `edit/downloads/manifest.json`.

## Which yt-dlp runs

`fetch.py` picks, in order:

1. `$VIDEO_EDIT_YTDLP` (a full command, e.g. `yt-dlp --cookies-from-browser chrome`)
2. the system `yt-dlp` if it is ≥ 2025.11.12
3. `uvx --from "yt-dlp[default]@latest" yt-dlp` — the newest release in a throwaway environment,
   no install (needs uv)
4. an older system yt-dlp, with a warning (YouTube will probably fail)

Since late 2025 YouTube needs a JavaScript runtime for its challenges. Deno is used when installed;
otherwise `fetch.py` enables Node ≥ 22 (`--js-runtimes node`). Homebrew/distro builds don't bundle
the challenge-solver scripts, so for a system yt-dlp it also allows `--remote-components ejs:github`
(yt-dlp fetches them from its own repo when needed). `pip install -U "yt-dlp[default]"` and the
official binaries bundle them.

To fix an outdated install permanently (ask first — it changes the user's system):
`brew upgrade yt-dlp && brew install deno` (or `pip install -U "yt-dlp[default]"`).

## Links the user gives

```bash
uv run --project <skill_dir> <skill_dir>/helpers/fetch.py url "<URL>" --edit-dir <project>/edit --purpose footage
uv run --project <skill_dir> <skill_dir>/helpers/fetch.py url "<URL>" --edit-dir <project>/edit --purpose broll --section 1:05-1:20
uv run --project <skill_dir> <skill_dir>/helpers/fetch.py url "<URL>" --edit-dir <project>/edit --purpose music --audio-only --section 0:10-1:10
```

- `--section` downloads only that slice (keyframe-accurate cut at download; the EDL trims exactly).
- Video is capped at 1080p (`--max-height`) and prefers H.264/AAC in MP4. Audio-only keeps the source
  codec (Opus/M4A) — ffmpeg reads both.
- A link the user supplied is recorded as `user-provided` unless it is Creative Commons; the user
  vouches for the rights. Downloaded footage is then a normal source: transcribe it, cut it, overlay it.
- Works for any site yt-dlp supports (YouTube, Vimeo, X, Instagram, TikTok, direct file URLs…).
  Private or members-only videos fail — ask for the file. Only if the user explicitly agrees,
  `VIDEO_EDIT_YTDLP='yt-dlp --cookies-from-browser chrome'` uses their browser session (it reads that
  browser's cookies).

## Finding b-roll yourself (Creative Commons only)

```bash
uv run --project <skill_dir> <skill_dir>/helpers/fetch.py search "aerial city night traffic" --edit-dir <project>/edit --n 8 --max-duration 300
```

- Searches YouTube with its Creative Commons filter, then **verifies each candidate's licence from its
  own metadata** and drops anything that isn't CC BY / CC0. Results below 720p are dropped
  (`--min-height`).
- Prints a numbered list and writes a thumbnail contact sheet (`edit/downloads/search/<query>.jpg`) —
  Read it to choose. Download the best one with `--found-by-search` and a `--section` around the
  usable seconds, then check it with `timeline_view.py` before placing it.
- Search like an editor: concrete, visual queries ("hands typing laptop close up", "barista pouring
  latte art slow motion"), 2–3 variants, and prefer clips that match the footage's light and colour.
- Use b-roll as a cutaway **overlay** (`{"file": …, "start_in_output": …, "duration": …, "fit": "cover"}`)
  so the narration keeps playing; 1.5–3 s per cutaway; land the cut on the word it illustrates.
- `--any-license` exists only for footage the user says they hold rights to. Never use it on your own.

## Licences

| `rights` in the manifest | Meaning | Usable in a brand edit |
|---|---|---|
| `cc-by` | Creative Commons Attribution (YouTube's CC licence is CC BY 3.0) | Yes — credit it |
| `cc0` | Public domain dedication | Yes |
| `user-provided` | The user's link, not CC | Yes, on the user's word |
| `cc-restricted` | NonCommercial / NoDerivatives | **No** — brand work is commercial and edited |
| `other` / `unknown` | Not CC, found by search | **No** |

`fetch.py credits --edit-dir edit` writes `edit/credits.md` for downloads the EDL actually uses and
lists anything that still needs a rights check. Deliver it with the video (CC BY needs the credit in
the post description or end card).

## Public-domain images (Wikimedia Commons)

Good for real-object graphics: US government works (currency, NASA imagery), old artworks, CC0
photos.
1. Find the file, and read its licence, through the API. Send a descriptive User-Agent:
   `curl -s -G https://commons.wikimedia.org/w/api.php -A "video-edit/1.0" --data-urlencode
   action=query --data-urlencode "titles=File:<name>" --data-urlencode prop=imageinfo
   --data-urlencode "iiprop=url|size|extmetadata" --data-urlencode iiurlwidth=1920
   --data-urlencode format=json`. `extmetadata.LicenseShortName` must say Public domain, CC0 or CC BY.
   Use `list=search&srnamespace=6&srsearch=…` to find titles.
2. Download the `thumburl` (1920 px) or the original `url` with `fetch.py url "<direct file URL>"`.
   The manifest records direct files as `user-provided`. Add the real licence to `credits.md`
   yourself.
3. Commons rate-limits bursts (HTTP 429 after about 4 quick requests). Wait about 60 s, then retry
   one at a time.

Currency images: US law allows reproductions that are clearly not counterfeit-usable. On-screen
motion graphics are fine; don't output print-size, both-sides, actual-size stills.

## Music

The user's tracks first, then the brand's sonic assets, then CC music (`search "… creative commons
music"`, `url --audio-only --section`). Put beds in the EDL `audio` list with `"duck": true` and fades.
YouTube "no copyright" in a title means nothing — trust only the verified licence field.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `HTTP Error 403`, `nsig`, challenge errors | Outdated yt-dlp or no JS runtime: let fetch.py use uvx, or `brew upgrade yt-dlp && brew install deno` |
| `Sign in to confirm you're not a bot` | Retry later; with the user's explicit OK use `--cookies-from-browser` via `VIDEO_EDIT_YTDLP` |
| `Requested format is not available` | `--max-height 720`; update yt-dlp |
| Section download is a few frames long or empty | Widen the section; some sites don't support sections — download whole and trim in the EDL |
| Downloaded file is VFR / odd fps | Fine — render.py re-times every segment to the output fps |
