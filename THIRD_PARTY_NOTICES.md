# Third-Party Notices

FILM_TV_REMOTE itself is licensed under the MIT License (see `LICENSE`). At
runtime, and only when you enable the corresponding optional feature, it can
contact the following third-party services or use the following third-party
data and software. Each is owned by its respective rights holder and governed
by its own terms and licenses. This project is **not affiliated with or endorsed
by** any of them. You are responsible for complying with their terms.

---

## Metadata & artwork

### IMDb (ratings)
Ratings are looked up in the publicly published **IMDb datasets**
(https://datasets.imdbws.com/) via the IMDb title id, which is resolved through
IMDb's public suggestion endpoint.

> Information courtesy of IMDb (https://www.imdb.com). Used with permission.

IMDb data is made available **for personal and non-commercial use only**. IMDb
is a trademark of IMDb.com, Inc. or its affiliates. See the IMDb Conditions of
Use: https://www.imdb.com/conditions and the non-commercial licensing terms
that accompany the datasets.

### Wikipedia / Wikimedia (posters)
Poster images are the article lead images fetched via the MediaWiki API of the
English Wikipedia (https://en.wikipedia.org/w/api.php). Many film posters on
Wikipedia are **non-free / copyrighted** works used there under fair-use
rationales; the copyright belongs to the film's studio or distributor. This
software downloads them **only to a local cache for personal identification of
your own files** and does not redistribute them. Article text is available under
CC BY-SA; see https://foundation.wikimedia.org/wiki/Terms_of_Use.

### OMDb API (optional, extra ratings)
Optional. Only used if you provide your own free OMDb API key. See
https://www.omdbapi.com/ for terms.

## Subtitles

### OpenSubtitles
Subtitles are searched and downloaded via the OpenSubtitles REST endpoint
(`rest.opensubtitles.org`). Subtitles are **user-contributed** works with their
own copyrights. Use is subject to OpenSubtitles' terms
(https://www.opensubtitles.org/). Please respect their rate limits and consider
supporting the project. This software downloads subtitles **for your personal
use with your own media only**.

### Google Translate (unofficial endpoint)
Optional machine translation (e.g. English -> Ukrainian) uses the public
`translate.googleapis.com` endpoint used by web widgets. This is **not an
official, supported or commercial API**. It is used here for personal,
low-volume, non-commercial convenience only, may change or stop working at any
time, and should not be relied upon for production use. For anything beyond
personal use, use the official Google Cloud Translation API under its terms.
"Google" and "Google Translate" are trademarks of Google LLC.

## Subtitle synchronization (optional feature)

### ffsubsync
The "sync subtitles to video" feature shells out to **ffsubsync**
(https://github.com/smacke/ffsubsync) by Stephen Macke, licensed under the MIT
License. Install separately: `pip install ffsubsync`.

### FFmpeg (via static-ffmpeg)
ffsubsync uses **FFmpeg** (https://ffmpeg.org) to read audio. FFmpeg binaries
are obtained here through the **static-ffmpeg** Python package
(https://pypi.org/project/static-ffmpeg/). FFmpeg is free software licensed
under the **LGPL v2.1+** / **GPL v2+** depending on build configuration; see
https://ffmpeg.org/legal.html. FFmpeg is a trademark of Fabrice Bellard. This
project does **not** bundle FFmpeg; it is downloaded on demand by static-ffmpeg
and remains under its own license.

## Playback

### VLC
For formats browsers cannot play (e.g. many MKV/HEVC files) the app offers an
`.m3u` playlist and direct HTTP URLs for use with **VLC media player**
(https://www.videolan.org/), a product of the VideoLAN non-profit, licensed
under GPLv2+. VLC is not bundled and is used only via user-opened URLs.

## Runtime

The core server uses only the **Python Standard Library**
(https://www.python.org/), licensed under the PSF License.

---

If you are a rights holder and believe something here is handled incorrectly,
please open an issue on the project page and it will be addressed.
