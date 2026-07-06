# FILM_TV_REMOTE 🎬📺

A tiny, **zero-dependency** local media server that streams **your own** movies
to any device on your home network — phone, laptop, smart-TV browser or Xbox
(via VLC) — with a clean mobile UI, real movie posters, IMDb ratings, subtitle
management and a **"phone as a TV remote"** casting mode.

> Personal, self-hosted, offline. No accounts, no cloud, no API keys required.

The whole server is a **single Python file** using only the standard library.
One optional feature (audio-based subtitle sync) needs two extra packages.

---

## ✨ Features

- **📁 Web gallery** of all videos in a folder (recursive), with search.
- **🖼️ Real posters** fetched from Wikipedia — no API key needed. Falls back to
  a coloured tile when no poster is found.
- **⭐ IMDb ratings** (stars + %) fetched from IMDb's public datasets — no API
  key needed.
- **▶️ In-browser player** with a subtitle picker; plays MP4/H.264 (and HEVC
  where the browser/TV supports it).
- **💬 Subtitles**
  - Auto-detects subtitle files next to each video (incl. a `Subs/` subfolder),
    with language labels.
  - **Download** Czech & Ukrainian subtitles on demand (OpenSubtitles).
  - **Translate** English → Ukrainian keeping the exact timing (Google Translate).
  - **Sync to video by audio** using `ffsubsync` for subtitles that are shifted
    or have the wrong framerate (optional feature).
  - New `.srt` files dropped into a folder appear **live** in the player.
- **📺 Cast to TV** — open `/tv` in a TV's web browser; your phone becomes the
  remote (play/pause, seek, choose subtitles). The TV page also reports whether
  it supports fullscreen and which codecs it can play.
- **🎞️ VLC playlist** — `/all.m3u` opens every movie in VLC (which plays any
  format, MKV/HEVC included) with subtitles attached.
- **🔄 Auto-refresh** — new files added to the folder are picked up automatically.

---

## 🚀 Quick start

Requires **Python 3.8+** (tested on 3.11, Windows).

```bash
# 1) Tell it where your videos are (edit MEDIA_ROOT in filmy_server.py)
#    or set an environment variable:
#      Windows (PowerShell):  $env:FILMY_ROOT = "D:\Movies"
#      Linux/macOS:           export FILMY_ROOT="/mnt/movies"

# 2) Run it
python filmy_server.py
```

On Windows you can just double-click **`START.cmd`**.

Then open the address shown in the console, e.g. `http://192.168.1.10:8099/`,
from any device on your network.

### Optional: subtitle "sync to video" feature

```bash
pip install ffsubsync static-ffmpeg
```

This lets the app align an out-of-sync subtitle to the film's audio. Without it,
every other feature still works.

---

## ⚙️ Configuration

Set in `filmy_server.py` (top of the file) or via environment variables:

| Setting        | Env var        | Default        | Meaning                                  |
|----------------|----------------|----------------|------------------------------------------|
| `MEDIA_ROOT`   | `FILMY_ROOT`   | `C:\FILMY`     | Folder with your videos (served)         |
| `PORT`         | `FILMY_PORT`   | `8099`         | HTTP port                                |
| `OMDB_API_KEY` | `OMDB_API_KEY` | *(empty)*      | Optional extra IMDb-rating source        |

---

## 🗺️ Endpoints (for the curious)

| Path            | What it does                                    |
|-----------------|-------------------------------------------------|
| `/`             | Movie gallery (mobile-friendly)                 |
| `/play?f=…`     | In-browser player for one film                  |
| `/tv`           | Fullscreen TV player (controlled from a phone)  |
| `/remote?f=…`   | Phone remote for the `/tv` page                 |
| `/all.m3u`      | Playlist of everything, for VLC                 |
| `/media/…`      | Video/subtitle file streaming (HTTP range)      |
| `/vtt/…`        | An `.srt` converted to WebVTT on the fly        |

---

## ⚠️ Important — please read

This is **software only**. It ships **no movies, subtitles, posters or ratings**.
Use it for **personal, private, non-commercial** playback of media **you are
legally entitled to**, on **your own local network** (it serves files over plain
HTTP with no authentication — never expose it to the public internet).

Optional online features contact third-party services (Wikipedia, IMDb data,
OpenSubtitles, Google Translate) — each has its **own terms and licenses**. By
using those features you agree to comply with them.

See **[DISCLAIMER.md](DISCLAIMER.md)** and
**[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** for the full details.

---

## 📄 License

MIT © 2026 Petr Závorka — see [LICENSE](LICENSE).

The MIT License covers the project's own source code only, not the third-party
services, data or binaries it can use at runtime.
