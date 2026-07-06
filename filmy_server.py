# -*- coding: utf-8 -*-
"""
FILM_TV_REMOTE - a small, zero-dependency local media server for your home network.

Stream your OWN movies from a folder to a phone, TV browser or Xbox (VLC).
Features: web gallery with posters (Wikipedia) and IMDb ratings, in-browser player
with subtitle selection, subtitle download & translation (OpenSubtitles + Google
Translate), audio-based subtitle sync (ffsubsync), an .m3u playlist for VLC, and a
"cast to TV" mode where the phone acts as a remote for a TV browser.

Quick start:
  1) Set the folder with your videos (MEDIA_ROOT below) or env var FILMY_ROOT.
  2) Run:  python filmy_server.py
  3) Open http://<this-pc-ip>:<PORT>/ on any device on your network.

The core server needs only the Python standard library. The optional
"sync subtitles to video" feature also needs:  pip install ffsubsync static-ffmpeg

Copyright (c) 2026 Petr Zavorka. Released under the MIT License (see LICENSE).
Project: https://github.com/petroza/FILM_TV_REMOTE

For PERSONAL use with media you are legally entitled to. This software does NOT
include or distribute any movies, subtitles or artwork. Third-party services and
data have their own terms - see THIRD_PARTY_NOTICES.md.
"""

import os
import re
import sys
import json
import gzip
import time
import socket
import hashlib
import tempfile
import threading
import subprocess
import mimetypes
import urllib.parse
import urllib.request
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------- CONFIG -----------------
# Folder with your videos. Edit it here, or set the FILMY_ROOT environment variable.
MEDIA_ROOT = os.environ.get("FILMY_ROOT") or r"C:\FILMY"
PORT = int(os.environ.get("FILMY_PORT", "8099"))
# Optional OMDb key = one extra IMDb-rating source. Posters + ratings work WITHOUT it.
# Free key (optional): https://www.omdbapi.com/apikey.aspx
OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm",
             ".ts", ".wmv", ".flv", ".mpg", ".mpeg", ".m2ts"}
# ------------------------------------------

mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/x-matroska", ".mkv")


def get_lan_ip():
    """Zjisti LAN IP adresu tohoto PC."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def list_videos():
    """Vrati seznam (rel_path, velikost) vsech videi rekurzivne, serazeny."""
    items = []
    for root, dirs, files in os.walk(MEDIA_ROOT):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXT:
                full = os.path.join(root, f)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                rel = os.path.relpath(full, MEDIA_ROOT).replace("\\", "/")
                items.append((rel, size))
    items.sort(key=lambda x: x[0].lower())
    return items


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def safe_path(rel):
    """Prevede rel URL cestu na absolutni a overi, ze je uvnitr MEDIA_ROOT."""
    rel = urllib.parse.unquote(rel)
    full = os.path.realpath(os.path.join(MEDIA_ROOT, rel))
    root = os.path.realpath(MEDIA_ROOT)
    if full == root or full.startswith(root + os.sep):
        return full
    return None


# jazyky preferovane pro titulky (cestina prvni)
_CZ_HINTS = ("cz", "cs", "cze", "czech", "cesky", "ces")

# preklad jazykovych kodu z nazvu souboru na hezky popisek
_LANG_LABELS = {
    "cz": "Cesky", "cs": "Cesky", "cze": "Cesky", "ces": "Cesky",
    "czech": "Cesky", "cesky": "Cesky",
    "en": "Anglicky", "eng": "Anglicky", "english": "Anglicky",
    "sk": "Slovensky", "svk": "Slovensky", "slo": "Slovensky", "slovak": "Slovensky",
    "de": "Nemecky", "ger": "Nemecky", "deu": "Nemecky",
    "uk": "Ukrajinsky", "ukr": "Ukrajinsky", "ua": "Ukrajinsky", "ukrainian": "Ukrajinsky",
    "ru": "Rusky", "rus": "Rusky", "russian": "Rusky",
    "pl": "Polsky", "pol": "Polsky", "polish": "Polsky",
    "fr": "Francouzsky", "fre": "Francouzsky", "french": "Francouzsky",
    "es": "Spanelsky", "spa": "Spanelsky", "spanish": "Spanelsky",
    "it": "Italsky", "ita": "Italsky", "italian": "Italsky",
    "german": "Nemecky", "nl": "Nizozemsky", "dut": "Nizozemsky",
    "forced": "Forced", "sdh": "SDH", "hi": "SDH",
}


_GLUED_CODES = ("cze", "cz", "cs", "ces", "eng", "en", "ukr", "uk", "ua",
                "slo", "svk", "sk", "ger", "deu", "de", "rus", "ru",
                "pol", "pl", "fre", "fr", "spa", "es", "ita", "it")


def _lang_label(fname, vname):
    """Odhadne jazyk/popisek titulku z nazvu souboru."""
    low = os.path.splitext(fname)[0].lower()
    extra = low[len(vname):] if low.startswith(vname) else low
    tokens = [t for t in re.split(r"[.\s_\-\[\]()0-9]+", extra) if t]
    labels = []
    for t in tokens:
        if t in _LANG_LABELS and _LANG_LABELS[t] not in labels:
            labels.append(_LANG_LABELS[t])
    if labels:
        lab = " ".join(labels)
        if "srovnane" in tokens:
            lab += " (srovnane)"
        elif "sync" in tokens:
            lab += " (sedici)"
        return lab
    # prilepeny kod na konci (napr. "pressureen" -> en)
    for code in _GLUED_CODES:
        if extra.endswith(code) and len(extra) > len(code):
            return _LANG_LABELS.get(code, code)
    tail = extra.strip(". _-")
    return tail if (0 < len(tail) <= 12) else "Titulky"


def find_subtitles(video_rel):
    """Najde .srt titulky k videu. Kdyz je film ve vlastni slozce (jen 1 video),
    vezme VSECHNY .srt v ni i v podslozce Subs. Jinak podle shody nazvu.
    Vraci seznam dictu {rel, label, cz}. Ceske prvni."""
    vfull = os.path.join(MEDIA_ROOT, video_rel.replace("/", os.sep))
    vdir = os.path.dirname(vfull)
    vname = os.path.splitext(os.path.basename(vfull))[0].lower()
    try:
        entries = os.listdir(vdir)
    except OSError:
        entries = []
    # patri slozka jednomu filmu? (pak jsou vsechny titulky jeho)
    own = sum(1 for f in entries
              if os.path.splitext(f)[1].lower() in VIDEO_EXT) == 1
    result = []
    seen = set()

    def consider(fpath, fname, force):
        if not fname.lower().endswith(".srt"):
            return
        sbase = os.path.splitext(fname)[0].lower()
        if not (force or sbase.startswith(vname) or vname.startswith(sbase)):
            return
        rel = os.path.relpath(fpath, MEDIA_ROOT).replace("\\", "/")
        if rel in seen:
            return
        seen.add(rel)
        is_cz = any(h in fname.lower() for h in _CZ_HINTS)
        result.append({"rel": rel, "label": _lang_label(fname, vname), "cz": is_cz})

    for f in entries:
        consider(os.path.join(vdir, f), f, own)
    for subdir in ("Subs", "Subtitles"):
        d = os.path.join(vdir, subdir)
        if os.path.isdir(d):
            try:
                for f in os.listdir(d):
                    consider(os.path.join(d, f), f, own)
            except OSError:
                pass
    result.sort(key=lambda d: (0 if d["cz"] else 1, d["label"].lower()))
    return result


def find_subtitle(video_rel):
    """Vrati nejlepsi (ceske) titulky nebo None - pro M3U playlist."""
    subs = find_subtitles(video_rel)
    return subs[0]["rel"] if subs else None


IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# balastni obrazky (bannery torrent-webu apod.) - nepouzivat jako plakat
_JUNK_IMG = ("yts", "yify", "www", "rarbg", "eztv", "1337", "sample",
             "screen", "proof", "banner", "torrent", "readme")


def find_poster(video_rel):
    """Najde obrazek (plakat) ve slozce filmu. Vraci rel cestu nebo None.
    Ignoruje balastni bannery (YTS apod.)."""
    vfull = os.path.join(MEDIA_ROOT, video_rel.replace("/", os.sep))
    vdir = os.path.dirname(vfull)
    vname = os.path.splitext(os.path.basename(vfull))[0].lower()
    imgs = []
    try:
        for f in os.listdir(vdir):
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                low = f.lower()
                if any(j in low for j in _JUNK_IMG):
                    continue
                imgs.append(f)
    except OSError:
        return None
    if not imgs:
        return None

    def score(fn):
        low = fn.lower()
        if any(k in low for k in ("poster", "cover", "folder")):
            return 0
        if os.path.splitext(low)[0].startswith(vname):
            return 1
        return 2

    imgs.sort(key=score)
    return os.path.relpath(os.path.join(vdir, imgs[0]), MEDIA_ROOT).replace("\\", "/")


def poster_hue(s):
    """Deterministicky odvodi barevny odstin z nazvu (pro dlazdici bez plakatu)."""
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % 360


# tokeny kvality/kodovani - vse od nich dal je "smeti" v nazvu
_QUALITY_RE = re.compile(
    r"\b(1080p|2160p|720p|480p|4k|x264|x265|h\.?264|h\.?265|hevc|avc|"
    r"webrip|web-?dl|web|bluray|brrip|bdrip|hdtv|dvdrip|hdrip|"
    r"amzn|nf|dsnp|hmax|atvp|ddp?5|dd5|aac|ac3|dts|eac3|10bit|8bit|hdr|"
    r"remux|proper|repack|extended|imax|multi|dual)\b",
    re.I,
)


def _clean_string(base):
    """Z jednoho release-nazvu udela citelny nazev + rok."""
    s = base.replace(".", " ").replace("_", " ")
    # rok: vezmi POSLEDNI vyskyt (release rok byva za nazvem), ne na zacatku
    year, year_pos = "", None
    for m in re.finditer(r"\b(19|20)\d{2}\b", s):
        val = int(m.group(0))
        if 1920 <= val <= 2027 and m.start() > 0:
            year, year_pos = m.group(0), m.start()
    cut = len(s)
    if year_pos is not None:
        cut = min(cut, year_pos)
    qm = _QUALITY_RE.search(s)
    if qm:
        cut = min(cut, qm.start())
    title = s[:cut].strip(" -[](){}")
    if not title:
        title = s.strip()
    title = re.sub(r"\s{2,}", " ", title)
    return title, year


def clean_title(rel):
    """Z release-nazvu (vc. slozky) udela citelny nazev + rok."""
    fname = os.path.splitext(os.path.basename(rel))[0]
    title, year = _clean_string(fname)
    # kryptický nazev souboru (napr. "20D5") -> pouzij jmeno slozky
    parent = os.path.dirname(rel)
    if parent and (len(title) < 4 or not re.search(r"[A-Za-z]{3}", title)):
        ptitle, pyear = _clean_string(os.path.basename(parent))
        if ptitle and re.search(r"[A-Za-z]{3}", ptitle):
            title, year = ptitle, (year or pyear)
    return title, year


def quality_badges(fname):
    """Vrati seznam odznaku (rozliseni, kodek)."""
    low = fname.lower()
    b = []
    if "2160p" in low or "4k" in low:
        b.append("4K")
    elif "1080p" in low:
        b.append("1080p")
    elif "720p" in low:
        b.append("720p")
    if "x265" in low or "hevc" in low or "h265" in low or "h 265" in low:
        b.append("HEVC")
    elif "x264" in low or "h264" in low or "h 264" in low or "avc" in low:
        b.append("H.264")
    return b


_TS = r"(\d{1,2}:\d{2}:\d{2})[,.](\d{3})"
_CUE_RE = re.compile(
    _TS + r"\s*-->\s*" + _TS +
    r"(.*?)(?=\n[ \t]*(?:\d+[ \t]*\n)?" + _TS + r"\s*-->|\Z)", re.S)
_ASS_TAG = re.compile(r"\{\\[^}]*\}")
# reklamni cue (bannery titulkovych webu) - preskocit
_AD_CUE = re.compile(
    r"opensubtitles|yts\b|yts\.|yify|rarbg|eztv|1337x|downloaded from|"
    r"api\.opensub|tryray|osdb|advertise your|support us and become|"
    r"provided by|synced? by|corrected by|ripped by",
    re.I)


def srt_to_vtt(data):
    """Prevede SRT bajty na cisty WebVTT. Robustni: poradi si s ruznym kodovanim,
    nestandardnimi prazdnymi radky i ASS tagy ({\\an8})."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1250", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = ["WEBVTT", ""]
    for m in _CUE_RE.finditer(text):
        start = m.group(1) + "." + m.group(2)
        end = m.group(3) + "." + m.group(4)
        body = _ASS_TAG.sub("", m.group(5))
        lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        # odstran koncove cislo (identifikator dalsiho titulku, co unikl u divnych SRT)
        while lines and lines[-1].isdigit():
            lines.pop()
        if not lines or _AD_CUE.search(" ".join(lines)):
            continue
        out.append(f"{start} --> {end}")
        out.extend(lines)
        out.append("")
    return "\n".join(out) + "\n"


# ==================== METADATA z OMDb / IMDB ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
META_FILE = os.path.join(CACHE_DIR, "meta.json")

_meta = {}
_meta_lock = threading.Lock()
_enrich_started = False


def _meta_key(title, year):
    return (title.lower().strip() + "|" + (year or "")).strip("|")


def load_meta():
    global _meta
    try:
        with open(META_FILE, encoding="utf-8") as f:
            _meta = json.load(f)
    except Exception:
        _meta = {}


def save_meta():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = META_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_meta, f, ensure_ascii=False)
        os.replace(tmp, META_FILE)
    except Exception:
        pass


def _omdb_query(params):
    p = dict(params)
    p["apikey"] = OMDB_API_KEY
    url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(p)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FilmyServer"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def omdb_fetch(title, year):
    """Najde film na OMDb (IMDB). Vraci dict s daty nebo None."""
    if not OMDB_API_KEY:
        return None
    d = _omdb_query({"t": title, "y": year, "type": "movie"}) if year else None
    if not d or d.get("Response") != "True":
        d = _omdb_query({"t": title, "type": "movie"})
    if not d or d.get("Response") != "True":
        s = _omdb_query({"s": title, "y": year}) if year else _omdb_query({"s": title})
        if s and s.get("Response") == "True" and s.get("Search"):
            imdbid = s["Search"][0].get("imdbID")
            if imdbid:
                d = _omdb_query({"i": imdbid})
    return d if (d and d.get("Response") == "True") else None


_POSTER_UA = "FilmyServer/1.0 (local personal media server)"


def wiki_poster(title, year):
    """Najde plakat filmu na Wikipedii (hlavni obrazek clanku) - BEZ klice.
    Vraci URL obrazku nebo None."""
    terms = [f"{title} film"]
    if year:
        terms.append(f"{title} {year} film")
    for term in terms:
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": term, "gsrlimit": "1",
            "prop": "pageimages", "piprop": "original", "pilicense": "any",
        })
        url = "https://en.wikipedia.org/w/api.php?" + q
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _POSTER_UA})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
        pages = (d.get("query") or {}).get("pages") or {}
        for p in pages.values():
            img = (p.get("original") or {}).get("source")
            if img:
                return img
    return None


def download_poster(poster_url, key):
    if not poster_url or poster_url == "N/A":
        return None
    fn = hashlib.md5(key.encode("utf-8")).hexdigest() + ".jpg"
    path = os.path.join(CACHE_DIR, fn)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return fn
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        req = urllib.request.Request(poster_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) < 200:
            return None
        with open(path, "wb") as out:
            out.write(data)
        return fn
    except Exception:
        return None


# ---- IMDb hodnoceni BEZ klice: suggestion API (ID) + oficialni dataset (rating) ----
RATINGS_GZ = os.path.join(CACHE_DIR, "imdb_ratings.tsv.gz")


def imdb_suggest(title, year):
    """Najde IMDb ID filmu (tt...) pres suggestion API. Bez klice. None kdyz nic."""
    try:
        url = ("https://v3.sg.media-imdb.com/suggestion/x/"
               + urllib.parse.quote(title) + ".json?includeVideos=0")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    cands = [it for it in d.get("d", []) if str(it.get("id", "")).startswith("tt")]
    if not cands:
        return None
    tl = title.lower()

    def score(it):
        s = 0.0
        if (it.get("l") or "").lower() == tl:
            s -= 100
        if year and str(it.get("y")) == year:
            s -= 50
        if it.get("qid") in ("movie", "tvMovie", "video", "tvSeries"):
            s -= 10
        s += (it.get("rank") or 999999) / 100000.0
        return s

    cands.sort(key=score)
    return cands[0].get("id")


def ensure_ratings_file():
    """Stahne/obnovi IMDb ratings dataset (max 1x za den). Vraci True kdyz je k dispozici."""
    try:
        fresh = (os.path.exists(RATINGS_GZ)
                 and (time.time() - os.path.getmtime(RATINGS_GZ) < 86400))
        if not fresh:
            os.makedirs(CACHE_DIR, exist_ok=True)
            req = urllib.request.Request("https://datasets.imdbws.com/title.ratings.tsv.gz",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            with open(RATINGS_GZ, "wb") as f:
                f.write(data)
    except Exception:
        pass
    return os.path.exists(RATINGS_GZ)


def lookup_ratings(tconsts):
    """Vrati {tconst: 'rating'} pro zadanou mnozinu ID z datasetu."""
    want = set(tconsts)
    out = {}
    if not want or not ensure_ratings_file():
        return out
    try:
        with gzip.open(RATINGS_GZ, "rt", encoding="utf-8", errors="replace") as f:
            for ln in f:
                i = ln.find("\t")
                if i < 0:
                    continue
                tid = ln[:i]
                if tid in want:
                    parts = ln.rstrip("\n").split("\t")
                    if len(parts) >= 2:
                        out[tid] = parts[1]
                    if len(out) == len(want):
                        break
    except Exception:
        pass
    return out


def get_meta(rel):
    title, year = clean_title(rel)
    with _meta_lock:
        return _meta.get(_meta_key(title, year))


def enrich_all():
    """Na pozadi BEZ klice: plakat (Wikipedia) + IMDb ID (suggestion) + IMDb rating (dataset)."""
    # --- FAZE 1: plakat + IMDb ID pro kazdy film ---
    for rel, _size in list_videos():
        title, year = clean_title(rel)
        key = _meta_key(title, year)
        with _meta_lock:
            ex = dict(_meta[key]) if key in _meta else None
        have_poster = bool(ex and ex.get("poster"))
        have_imdb = bool(ex and ex.get("imdb_done"))
        if have_poster and have_imdb:
            continue
        if ex and ex.get("tries", 0) >= 6:
            continue
        entry = ex or {"title": title, "year": year, "poster": None,
                       "imdb": None, "rating": None}
        entry["title"], entry["year"], entry["done"] = title, year, True
        entry["tries"] = (ex.get("tries", 0) + 1) if ex else 1
        # plakat: Wikipedia (pripadne OMDb kdyz je klic)
        if not have_poster:
            art = wiki_poster(title, year)
            if not art and OMDB_API_KEY:
                data = omdb_fetch(title, year)
                if data and data.get("Poster") not in (None, "N/A"):
                    art = data.get("Poster")
            if art:
                pf = download_poster(art, key)
                if pf:
                    entry["poster"] = pf
        # IMDb ID pres suggestion API
        if not have_imdb:
            entry["imdb"] = imdb_suggest(title, year)
            entry["imdb_done"] = True
        with _meta_lock:
            _meta[key] = entry
        save_meta()
        time.sleep(1.2)   # setrnost (Wikipedia/IMDb rate-limit)
    # --- FAZE 2: IMDb rating z oficialniho datasetu ---
    with _meta_lock:
        need = {e["imdb"]: k for k, e in _meta.items()
                if e.get("imdb") and not e.get("rating") and e.get("rating_tries", 0) < 6}
    if need:
        ratings = lookup_ratings(set(need.keys()))
        got = 0
        with _meta_lock:
            for tid, k in need.items():
                if k in _meta:
                    r = ratings.get(tid)
                    if r:
                        _meta[k]["rating"] = r
                        got += 1
                    _meta[k]["rating_tries"] = _meta[k].get("rating_tries", 0) + 1
        save_meta()
        if got:
            print(f"  [imdb] doplneno {got} hodnoceni")


def _enrich_loop():
    """Prubezne: doplni plakaty pro NOVE filmy, hotove uz nestahuje (cache)."""
    while True:
        try:
            enrich_all()
        except Exception:
            pass
        time.sleep(600)   # kazdych 10 min zkontroluj, jestli pribyl film


def start_enrichment():
    global _enrich_started
    load_meta()
    if _enrich_started:
        return
    _enrich_started = True
    threading.Thread(target=_enrich_loop, daemon=True).start()


def video_poster_src(rel):
    """URL plakatu pro dlazdici: OMDb cache -> lokalni obrazek -> None."""
    m = get_meta(rel)
    if m and m.get("poster"):
        return "/cache/" + m["poster"]
    local = find_poster(rel)
    if local:
        return "/media/" + urllib.parse.quote(local)
    return None


def video_rating(rel):
    m = get_meta(rel)
    return m.get("rating") if m else None


# ==================== PROCHAZENI SLOZEK ====================
def count_videos_in(abspath):
    n = 0
    for _root, _dirs, files in os.walk(abspath):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                n += 1
    return n


def list_level(rel):
    """(folders, videos) na dane urovni. rel='' = koren.
    folders=[(nazev, rel, pocet)], videos=[(rel, size)]."""
    base = safe_path(rel) if rel else os.path.realpath(MEDIA_ROOT)
    folders, videos = [], []
    if not base or not os.path.isdir(base):
        return folders, videos
    try:
        names = os.listdir(base)
    except OSError:
        return folders, videos
    for name in sorted(names, key=str.lower):
        full = os.path.join(base, name)
        childrel = (rel + "/" + name) if rel else name
        if os.path.isdir(full):
            cnt = count_videos_in(full)
            if cnt > 0:
                folders.append((name, childrel, cnt))
        elif os.path.splitext(name)[1].lower() in VIDEO_EXT:
            try:
                sz = os.path.getsize(full)
            except OSError:
                sz = 0
            videos.append((childrel, sz))
    return folders, videos


def videos_under(rel, limit=4):
    """Prvnich N videi ve slozce rekurzivne (pro nahled slozky)."""
    base = safe_path(rel) if rel else os.path.realpath(MEDIA_ROOT)
    root = os.path.realpath(MEDIA_ROOT)
    out = []
    if not base or not os.path.isdir(base):
        return out
    for r, dirs, files in os.walk(base):
        dirs.sort(key=str.lower)
        for f in sorted(files, key=str.lower):
            if os.path.splitext(f)[1].lower() in VIDEO_EXT:
                out.append(os.path.relpath(os.path.join(r, f), root).replace("\\", "/"))
                if len(out) >= limit:
                    return out
    return out


# ==================== STAVBA DLAZDIC + STRANKY ====================
def video_card_html(rel, size):
    title, year = clean_title(rel)
    badges = quality_badges(rel)
    subs = find_subtitles(rel)
    src = video_poster_src(rel)
    rating = video_rating(rel)
    skey = html.escape((title + " " + rel).lower())
    if src:
        thumb = f'<div class="poster" style="background-image:url(\'{src}\')"></div>'
    else:
        hue = poster_hue(title)
        initial = html.escape(title[:1].upper()) if title else "?"
        thumb = (f'<div class="poster ph" style="background:'
                 f'linear-gradient(150deg,hsl({hue},46%,34%),hsl({(hue + 40) % 360},52%,18%))">'
                 f'<span>{initial}</span></div>')
    # IMDb hodnoceni: hvezdicky + procenta (pod plakatem)
    imdb_html = ""
    if rating:
        try:
            val = float(rating)
            pct = round(val * 10)
            full = int(round(val / 2.0))
            stars = "".join(
                ('<span class="st on">&#9733;</span>' if i < full
                 else '<span class="st">&#9733;</span>') for i in range(5))
            imdb_html = (f'<div class="imdb">{stars}'
                         f'<b>{pct}%</b><span class="rn">IMDb {html.escape(rating)}</span></div>')
        except ValueError:
            imdb_html = ""
    meta = ""
    if year:
        meta += f'<span class="badge yr">{year}</span>'
    for b in badges:
        meta += f'<span class="badge">{html.escape(b)}</span>'
    if subs:
        langs = " ".join(sorted({s["label"].split()[0] for s in subs}))
        meta += f'<span class="badge sub">&#128172; {html.escape(langs)}</span>'
    return (f'<a class="card" href="/play?f={urllib.parse.quote(rel)}" data-name="{skey}">'
            f'<div class="pw">{thumb}</div>'
            f'<div class="info">{imdb_html}<div class="title">{html.escape(title)}</div>'
            f'<div class="meta">{meta}</div>'
            f'<div class="sz">{human_size(size)}</div></div></a>')


def folder_card_html(name, childrel, cnt):
    posters = []
    for vr in videos_under(childrel, 4):
        s = video_poster_src(vr)
        if s:
            posters.append(s)
    dname, _y = _clean_string(name)
    dname = dname or name
    if posters:
        cells = "".join(f'<div style="background-image:url(\'{p}\')"></div>' for p in posters[:4])
        thumb = f'<div class="poster mont">{cells}</div>'
    else:
        hue = poster_hue(name)
        thumb = (f'<div class="poster ph" style="background:'
                 f'linear-gradient(150deg,hsl({hue},40%,30%),hsl({(hue + 40) % 360},45%,16%))">'
                 f'<span>&#128193;</span></div>')
    skey = html.escape((dname + " " + name).lower())
    return (f'<a class="card" href="/folder?path={urllib.parse.quote(childrel)}" data-name="{skey}">'
            f'<div class="pw">{thumb}</div>'
            f'<div class="info"><div class="title">&#128193; {html.escape(dname)}</div>'
            f'<div class="meta"><span class="badge">{cnt} filmu</span></div></div></a>')


def _breadcrumb(rel):
    crumbs = ['<a href="/">&#127916; FILMY</a>']
    if rel:
        acc = ""
        for p in rel.split("/"):
            acc = (acc + "/" + p) if acc else p
            dn, _y = _clean_string(p)
            crumbs.append(f'<a href="/folder?path={urllib.parse.quote(acc)}">{html.escape(dn or p)}</a>')
    return ' <span class="sep">&rsaquo;</span> '.join(crumbs)


GRID_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Segoe UI,system-ui,Arial,sans-serif;background:#0b0c10;color:#e8e8ea;-webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:10;padding:12px 16px;background:rgba(11,12,16,.92);backdrop-filter:blur(8px);border-bottom:1px solid #1c1f2a}
.crumb{font-size:17px;font-weight:600;margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.crumb a{color:#e8e8ea;text-decoration:none}
.crumb a:last-child{color:#7db8ff}
.crumb .sep{opacity:.35}
.search{width:100%;padding:12px 14px;font-size:16px;background:#171922;border:1px solid #2a2e3d;border-radius:12px;color:#fff}
.search:focus{outline:none;border-color:#3b82f6}
.bar{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 16px 4px;font-size:13px;opacity:.65;flex-wrap:wrap}
.bar a{color:#7db8ff;text-decoration:none}
.banner{margin:8px 16px 0;padding:10px 12px;background:#2a2410;border:1px solid #6e5a24;border-radius:10px;font-size:12.5px;color:#ffd98a;line-height:1.5}
.banner a{color:#ffcf6b}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;padding:12px 16px 32px}
.card{display:flex;flex-direction:column;background:#14161d;border:1px solid #1e2230;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;transition:transform .1s,border-color .1s}
.card:active{transform:scale(.98)}
.card:hover{border-color:#3b82f6}
.pw{position:relative;width:100%}
.poster{width:100%;aspect-ratio:2/3;background:#20232e center/cover no-repeat}
.poster.ph{display:flex;align-items:center;justify-content:center}
.poster.ph span{font-size:52px;font-weight:700;color:#fff;opacity:.9;text-shadow:0 2px 10px rgba(0,0,0,.45)}
.poster.mont{display:grid;grid-template-columns:1fr 1fr;grid-auto-rows:1fr;gap:2px}
.poster.mont>div{background:#20232e center/cover no-repeat}
.rating{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.8);color:#ffd257;font-size:12.5px;font-weight:700;padding:3px 7px;border-radius:8px}
.info{padding:10px 11px 12px;display:flex;flex-direction:column;gap:7px;flex:1}
.imdb{display:flex;align-items:center;gap:2px;margin-bottom:1px}
.imdb .st{color:#3a3f4d;font-size:13px;line-height:1}
.imdb .st.on{color:#f5c518}
.imdb b{color:#f5c518;font-size:13px;margin-left:5px}
.imdb .rn{opacity:.45;font-size:10.5px;margin-left:auto}
.title{font-size:14.5px;font-weight:600;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:auto}
.badge{font-size:11px;padding:2px 7px;border-radius:6px;background:#232838;color:#c3c9d6;white-space:nowrap}
.badge.yr{background:#2b3350;color:#aebbff}
.badge.sub{background:#1c3a2a;color:#8ee6b0}
.sz{font-size:11px;opacity:.4}
@media(min-width:560px){.grid{grid-template-columns:repeat(3,1fr)}}
@media(min-width:800px){.grid{grid-template-columns:repeat(4,1fr)}}
@media(min-width:1100px){.grid{grid-template-columns:repeat(5,1fr)}}
"""

SEARCH_JS = """
function filter(q){
  q=q.toLowerCase().trim();var n=0;
  document.querySelectorAll('.card').forEach(function(c){
    var m=c.dataset.name.indexOf(q)>=0;c.style.display=m?'':'none';if(m)n++;
  });
  var el=document.getElementById('cnt');if(el)el.textContent=n+' polozek';
}
function tvOn(){return localStorage.getItem('tvmode')==='1';}
function applyTv(){
  var on=tvOn();
  document.querySelectorAll('a.card').forEach(function(c){
    var h=c.getAttribute('href')||'';
    if(on&&h.indexOf('/play?')===0)c.setAttribute('href',h.replace('/play?','/remote?'));
    if(!on&&h.indexOf('/remote?')===0)c.setAttribute('href',h.replace('/remote?','/play?'));
  });
  var b=document.getElementById('tvtoggle');
  if(b){b.textContent=on?'\\u{1F4FA} TV: ZAP':'\\u{1F4FA} TV: VYP';b.style.color=on?'#8ee6b0':'#7db8ff';}
}
function toggleTv(){localStorage.setItem('tvmode',tvOn()?'0':'1');applyTv();}
document.addEventListener('DOMContentLoaded',applyTv);
"""


def build_page(page_title, top_html, bar_html, grid_html):
    return (
        '<!doctype html><html lang="cs"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + page_title + '</title><style>' + GRID_CSS + '</style></head><body>'
        + top_html + bar_html
        + '<div class="grid" id="grid">' + grid_html + '</div>'
        + '<script>' + SEARCH_JS + '</script></body></html>'
    )


# ==================== CAST: ovladani TV z mobilu ====================
_cast_lock = threading.Lock()
# sdileny stav "co se hraje na TV"
_cast = {"ver": 0, "rel": None, "url": None, "title": "", "subs": [],
         "sub": -1, "paused": False, "seek": 0.0, "seekVer": 0}
# stav hlaseny z TV zpet (pro ovladac na mobilu)
_tv = {"time": 0.0, "dur": 0.0, "paused": True, "rel": None}


def cast_snapshot(d):
    with _cast_lock:
        return dict(d)


TV_PAGE = """<!doctype html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FILMY TV</title>
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;font-family:Segoe UI,Arial,sans-serif}
#tv{position:fixed;inset:0;width:100%;height:100%;background:#000;object-fit:contain}
#idle{position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#8a93a6;text-align:center;padding:5vw}
#idle h1{font-size:4.5vw;color:#e8e8ea;margin:0 0 2vh}
#idle p{font-size:2.2vw;margin:.4vh 0}
#cap{margin-top:4vh;font-size:1.9vw;opacity:.75;line-height:1.6}
#enable{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.88);color:#fff;font-size:3.5vw;text-align:center;z-index:5}
video::cue{background:rgba(0,0,0,.6);color:#fff;font-size:2.4vw}
</style></head><body>
<video id="tv" playsinline></video>
<div id="idle">
  <h1>&#128250; FILMY &ndash; TV pripravena</h1>
  <p>Pust film z mobilu a objevi se tady.</p>
  <div id="cap"></div>
</div>
<div id="enable">Stiskni OK / Enter na dalkovem pro spusteni &#9654;</div>
<script>
(function(){
var v=document.getElementById('tv'),idle=document.getElementById('idle'),enable=document.getElementById('enable');
var curVer=-1,curRel=null,curSeekVer=-1,curSub=-3;
try{
 var fsOK=!!(document.fullscreenEnabled||document.webkitFullscreenEnabled);
 var t=document.createElement('video');
 var hevc=t.canPlayType('video/mp4;codecs="hvc1"');
 var h264=t.canPlayType('video/mp4;codecs="avc1.42E01E"');
 document.getElementById('cap').textContent='Fullscreen: '+(fsOK?'ANO':'NE')+'  |  H.264: '+(h264||'ne')+'  |  HEVC: '+(hevc||'ne');
}catch(e){}
function goFS(){var el=document.documentElement;try{(el.requestFullscreen||el.webkitRequestFullscreen||function(){}).call(el);}catch(e){}}
function tryPlay(){var p=v.play();if(p&&p.catch){p.catch(function(){enable.style.display='flex';});}}
function enableNow(){enable.style.display='none';goFS();v.play();}
document.addEventListener('keydown',function(){if(enable.style.display==='flex')enableNow();else goFS();});
document.addEventListener('click',function(){if(enable.style.display==='flex')enableNow();else goFS();});
function setTrack(i){var tt=v.textTracks;for(var k=0;k<tt.length;k++)tt[k].mode=(k===i)?'showing':'hidden';}
function poll(){
 fetch('/cast/state',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
  if(s.ver!==curVer){
   curVer=s.ver;
   if(s.rel!==curRel){
    curRel=s.rel;
    while(v.firstChild)v.removeChild(v.firstChild);
    if(s.url){
     v.src=s.url;
     (s.subs||[]).forEach(function(su){var tr=document.createElement('track');tr.kind='subtitles';tr.srclang='cs';tr.label=su.l;tr.src=su.u;v.appendChild(tr);});
     v.load();idle.style.display='none';tryPlay();goFS();
    }else{v.removeAttribute('src');v.load();idle.style.display='flex';}
    curSub=-3;
   }
   if(s.sub!==curSub){curSub=s.sub;setTrack(s.sub);}
   if(s.paused&&!v.paused)v.pause();
   if(!s.paused&&v.paused&&v.src)tryPlay();
  }
  if(s.seekVer!==curSeekVer){curSeekVer=s.seekVer;if(isFinite(s.seek)){try{v.currentTime=s.seek;}catch(e){}}}
 }).catch(function(){});
}
setInterval(poll,1000);poll();
setInterval(function(){
 var d=isFinite(v.duration)?Math.floor(v.duration):0;
 fetch('/cast/report?t='+Math.floor(v.currentTime||0)+'&d='+d+'&p='+(v.paused?1:0)+'&rel='+encodeURIComponent(curRel||''),{cache:'no-store'}).catch(function(){});
},1500);
})();
</script></body></html>"""


REMOTE_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:Segoe UI,system-ui,Arial,sans-serif;background:#0b0c10;color:#e8e8ea}
.top{padding:14px 16px;border-bottom:1px solid #1c1f2a}
.top a{color:#7db8ff;text-decoration:none}
.wrap{padding:18px 16px;max-width:560px;margin:0 auto}
.title{font-size:20px;font-weight:700;margin:6px 0 2px}
.on{font-size:13px;color:#8ee6b0;margin-bottom:18px}
.row{display:flex;align-items:center;gap:8px;margin:14px 0}
.time{font-size:13px;opacity:.7;min-width:44px;text-align:center}
input[type=range]{flex:1;accent-color:#3b82f6;height:28px}
.btns{display:flex;justify-content:center;align-items:center;gap:14px;margin:22px 0}
.btn{background:#1b1e28;border:1px solid #2a2f3e;color:#fff;border-radius:14px;font-size:20px;padding:16px 20px;min-width:64px;cursor:pointer}
.btn.play{background:#2563eb;border-color:#2563eb;font-size:26px;min-width:84px}
.btn:active{transform:scale(.96)}
.sub{display:flex;align-items:center;gap:8px;margin:18px 0}
select{flex:1;padding:12px;font-size:16px;background:#171922;color:#fff;border:1px solid #2a2e3d;border-radius:10px}
.stop{display:block;width:100%;margin-top:10px;background:#3a1d1d;border:1px solid #6e2b2b;color:#ffb4b4;padding:14px;border-radius:12px;text-align:center;text-decoration:none;font-size:15px}
.acts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.act{display:flex;flex-direction:column;align-items:center;gap:6px;padding:13px 5px;border-radius:14px;border:1px solid #2a2f3e;background:#171922;color:#e8e8ea;cursor:pointer;font-size:13px;font-weight:600;line-height:1.2;text-align:center}
.act i{font-size:21px;font-style:normal;line-height:1}
.act small{display:block;font-size:11px;font-weight:400;opacity:.55;margin-top:2px}
.act:active{transform:scale(.96)}
.act.a1{border-color:#2f6e4a}
.act.a2{border-color:#2f4f7e}
.act.a3{border-color:#7e5a2f}
.substat{display:none;padding:8px 2px 0;font-size:13px;color:#9fb0c8}
.hint{opacity:.5;font-size:12.5px;margin-top:16px;line-height:1.5}
"""


def page_remote_html(rel, sub):
    title = html.escape(clean_title(rel)[0])
    subs = find_subtitles(rel)
    opts = '<option value="-1">Titulky vypnuty</option>'
    for i, s in enumerate(subs):
        selattr = " selected" if i == sub else ""
        opts += f'<option value="{i}"{selattr}>{html.escape(s["label"])}</option>'
    relq = urllib.parse.quote(rel)
    subrels = json.dumps([s["rel"] for s in subs])
    js = ("var REL=" + json.dumps(rel) + ",SUB=" + str(sub)
          + ",SUBRELS=" + subrels + ";") + REMOTE_JS + FINDSUB_JS
    return (
        '<!doctype html><html lang="cs"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Ovladac</title><style>' + REMOTE_CSS + '</style></head><body>'
        '<div class="top"><a href="/">&larr; Zpet na filmy</a></div>'
        '<div class="wrap">'
        '<div class="title">' + title + '</div>'
        '<div class="on">&#128250; Hraje na TV</div>'
        '<div class="row"><span class="time" id="cur">0:00</span>'
        '<input type="range" id="seek" min="0" max="0" value="0">'
        '<span class="time" id="dur">0:00</span></div>'
        '<div class="btns">'
        '<button class="btn" onclick="back10()">&#9194; 10</button>'
        '<button class="btn play" id="pp" onclick="pp()">&#9208;</button>'
        '<button class="btn" onclick="fwd10()">10 &#9193;</button>'
        '</div>'
        '<div class="sub"><span>Titulky:</span>'
        '<select id="subsel" onchange="subch(this.value)">' + opts + '</select></div>'
        '<div class="acts">'
        '<button class="act a1" onclick="findSub()"><i>&#127760;</i><span>Dohledat<small>CZ + UK</small></span></button>'
        '<button class="act a2" onclick="syncSub()"><i>&#128260;</i><span>Prelozit<small>EN&rarr;UA</small></span></button>'
        '<button class="act a3" onclick="syncVid()"><i>&#127919;</i><span>Srovnat<small>na video</small></span></button>'
        '</div>'
        '<div class="substat" id="substat"></div>'
        '<a class="stop" href="javascript:void(0)" onclick="stopTv()">&#9209; Zastavit na TV</a>'
        '<div class="hint">Mobil je dalkove ovladani &ndash; film hraje na TV. '
        'Kdyby na TV nic nebylo, otevri na TV adresu <b>/tv</b>.</div>'
        '</div><script>' + js + '</script></body></html>'
    )


REMOTE_JS = """
var dur=0,paused=false,seeking=false;
function cmd(u){return fetch(u,{cache:'no-store'}).catch(function(){});}
cmd('/cast/cmd?a=play&f='+encodeURIComponent(REL)+'&sub='+SUB);
function fmt(s){s=Math.floor(s||0);if(!isFinite(s))s=0;var m=Math.floor(s/60),x=s%60;return m+':'+(x<10?'0':'')+x;}
function poll(){fetch('/cast/tv',{cache:'no-store'}).then(function(r){return r.json();}).then(function(t){
 dur=t.dur||0;paused=t.paused;
 document.getElementById('cur').textContent=fmt(t.time);
 document.getElementById('dur').textContent=fmt(dur);
 var sk=document.getElementById('seek');if(!seeking){sk.max=dur||0;sk.value=t.time||0;}
 document.getElementById('pp').innerHTML=paused?'&#9654;':'&#9208;';
}).catch(function(){});}
setInterval(poll,1000);poll();
function pp(){cmd('/cast/cmd?a='+(paused?'resume':'pause'));paused=!paused;}
function back10(){cmd('/cast/cmd?a=seek&t='+Math.max(0,(+document.getElementById('seek').value)-10));}
function fwd10(){cmd('/cast/cmd?a=seek&t='+((+document.getElementById('seek').value)+10));}
function stopTv(){cmd('/cast/cmd?a=stop');}
function subch(v){cmd('/cast/cmd?a=sub&i='+v);}
var sk=document.getElementById('seek');
sk.addEventListener('input',function(){seeking=true;document.getElementById('cur').textContent=fmt(+sk.value);});
sk.addEventListener('change',function(){seeking=false;cmd('/cast/cmd?a=seek&t='+(+sk.value));});
"""


# ==================== DOHLEDANI + PREKLAD TITULKU ====================
_SUB_UA = "FilmyServer v1"
_subjobs = {}
_subjobs_lock = threading.Lock()


def opensub_search(imdbid, title, lang):
    """Hleda titulky na OpenSubtitles (bez klice). lang = cze/eng. Vraci serazeny seznam."""
    urls = []
    if imdbid:
        urls.append("https://rest.opensubtitles.org/search/imdbid-"
                    + imdbid.replace("tt", "") + "/sublanguageid-" + lang)
    if title:
        urls.append("https://rest.opensubtitles.org/search/query-"
                    + urllib.parse.quote(title) + "/sublanguageid-" + lang)
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _SUB_UA,
                                                       "X-User-Agent": _SUB_UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
        if isinstance(data, list) and data:
            # jen SRT format (ne MicroDVD .sub)
            srt_only = [x for x in data if str(x.get("SubFormat", "")).lower() == "srt"]
            use = srt_only or data

            def dc(x):
                try:
                    return int(x.get("SubDownloadsCount") or 0)
                except (ValueError, TypeError):
                    return 0
            use.sort(key=dc, reverse=True)
            return use
    return []


_AD_RE = re.compile(
    r"opensubtitles|tryray|api\.OpenSubtitles|osdb\.link|advertise|"
    r"become a member|support us|watch any video|uploaded by|resync|"
    r"www\.|\.com\b|\.app\b|subtitles? by",
    re.I)


def clean_srt(text):
    """Vyhodi reklamni bloky a precisluje SRT. Pracuje jen kdyz je to SRT (ma '-->')."""
    if "-->" not in text:
        return text
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    out, n = [], 0
    for b in blocks:
        lines = b.splitlines()
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        body = " ".join(lines[ti + 1:])
        if _AD_RE.search(body):
            continue
        n += 1
        out.append(str(n) + "\n" + lines[ti] + "\n" + "\n".join(lines[ti + 1:]))
    return "\n\n".join(out) + "\n"


def _first_srt(results):
    """Z prvnich vysledku vezme prvni, co je opravdu SRT, ocistene."""
    for r in results[:4]:
        t = download_srt_text(r.get("SubDownloadLink"))
        if t and "-->" in t:
            return clean_srt(t)
    return None


def download_srt_text(dl_link):
    """Stahne a rozbali .srt z OpenSubtitles. Vraci text nebo None."""
    try:
        req = urllib.request.Request(dl_link, headers={"User-Agent": _SUB_UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
    except Exception:
        return None
    try:
        raw = gzip.decompress(raw)
    except Exception:
        pass
    for enc in ("utf-8-sig", "utf-8", "cp1250", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def gtranslate(text, sl, tl):
    """Prelozi text pres verejny Google endpoint (bez klice)."""
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl="
           + sl + "&tl=" + tl + "&dt=t&q=" + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return "".join(seg[0] for seg in d[0] if seg and seg[0])


def translate_srt(srt, sl, tl):
    """Prelozi text titulku, zachova PRESNE casovani zdroje (robustni parser)."""
    text = srt.replace("\r\n", "\n").replace("\r", "\n")
    cues = []  # (start, end, text)
    for m in _CUE_RE.finditer(text):
        s = m.group(1) + "," + m.group(2)
        e = m.group(3) + "," + m.group(4)
        body = _ASS_TAG.sub("", m.group(5))
        lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        while lines and lines[-1].isdigit():
            lines.pop()
        txt = " ".join(lines)
        if not txt or _AD_CUE.search(txt):
            continue
        cues.append((s, e, txt))
    texts = [c[2] for c in cues]
    translated = {}
    BATCH = 40
    for start in range(0, len(texts), BATCH):
        chunk = texts[start:start + BATCH]
        try:
            parts = gtranslate("\n".join(chunk), sl, tl).split("\n")
        except Exception:
            parts = []
        if len(parts) == len(chunk):
            for j, t in enumerate(parts):
                translated[start + j] = t
        else:
            for j, orig in enumerate(chunk):
                try:
                    translated[start + j] = gtranslate(orig, sl, tl)
                except Exception:
                    translated[start + j] = orig
                time.sleep(0.15)
        time.sleep(0.3)
    out = []
    for i, (s, e, _t) in enumerate(cues):
        out.append(f"{i + 1}\n{s} --> {e}\n{translated.get(i, '')}")
    return "\n\n".join(out) + "\n"


def _sub_set(rel, state, msg):
    with _subjobs_lock:
        _subjobs[rel] = {"state": state, "msg": msg}


def _safe_target(base, tag):
    """Vrati cestu, ktera JESTE neexistuje (nikdy neprepise existujici titulek)."""
    p = base + "." + tag + ".srt"
    if not os.path.exists(p):
        return p
    i = 2
    while os.path.exists(f"{base}.{tag}{i}.srt"):
        i += 1
    return f"{base}.{tag}{i}.srt"


def _subfetch_worker(rel):
    try:
        title, year = clean_title(rel)
        m = get_meta(rel)
        imdbid = (m or {}).get("imdb")
        vfull = os.path.join(MEDIA_ROOT, rel.replace("/", os.sep))
        base = os.path.splitext(vfull)[0]
        subs_now = find_subtitles(rel)
        has_cz = any(s.get("cz") for s in subs_now)
        has_uk = any("Ukrajinsky" in s.get("label", "") for s in subs_now)
        added = []
        # 1) ceske titulky (kdyz jeste nejsou)
        if not has_cz:
            _sub_set(rel, "running", "Hledam ceske titulky...")
            srt = _first_srt(opensub_search(imdbid, title, "cze"))
            if srt:
                with open(_safe_target(base, "cs"), "w", encoding="utf-8") as f:
                    f.write(srt)
                added.append("ceske")
        # 2) ukrajinske titulky (kdyz jeste nejsou)
        if not has_uk:
            _sub_set(rel, "running", "Hledam ukrajinske titulky...")
            srt = _first_srt(opensub_search(imdbid, title, "ukr"))
            if srt:
                with open(_safe_target(base, "uk"), "w", encoding="utf-8") as f:
                    f.write(srt)
                added.append("ukrajinske")
            else:
                # zaloha: anglicke -> preklad do ukrajinstiny
                _sub_set(rel, "running", "Ukrajinske nejsou, prekladam z anglictiny...")
                en = _first_srt(opensub_search(imdbid, title, "eng"))
                if en:
                    uk = translate_srt(en, "en", "uk")
                    with open(_safe_target(base, "uk"), "w", encoding="utf-8") as f:
                        f.write(uk)
                    added.append("ukrajinske (prelozene z EN)")
        # vysledek
        if added:
            _sub_set(rel, "done", "Hotovo: doplneno " + ", ".join(added) + ".")
        elif has_cz and has_uk:
            _sub_set(rel, "done", "Film uz ma ceske i ukrajinske titulky.")
        else:
            _sub_set(rel, "failed", "Zadne dalsi titulky nenalezeny.")
    except Exception as e:
        _sub_set(rel, "failed", "Chyba: " + str(e)[:80])


def _read_local_srt(sub_rel):
    """Nacte lokalni .srt jako text (detekce kodovani)."""
    p = os.path.join(MEDIA_ROOT, sub_rel.replace("/", os.sep))
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _subtranslate_worker(rel):
    """Prelozi anglicke titulky filmu do ukrajinstiny se SPRAVNYM (anglickym) casovanim."""
    try:
        _sub_set(rel, "running", "Hledam anglicke titulky u filmu...")
        subs = find_subtitles(rel)
        eng = next((s for s in subs if "Anglicky" in s["label"] and "SDH" not in s["label"]), None)
        if not eng:
            eng = next((s for s in subs if "Anglicky" in s["label"]), None)
        if not eng:
            _sub_set(rel, "failed", "Film nema anglicke titulky k prekladu.")
            return
        srt = _read_local_srt(eng["rel"])
        if not srt or "-->" not in srt:
            _sub_set(rel, "failed", "Nepodarilo se nacist anglicke titulky.")
            return
        srt = clean_srt(srt)
        _sub_set(rel, "running", "Prekladam do ukrajinstiny (podle anglickeho casovani)...")
        uk = translate_srt(srt, "en", "uk")
        vfull = os.path.join(MEDIA_ROOT, rel.replace("/", os.sep))
        base = os.path.splitext(vfull)[0]
        with open(_safe_target(base, "sync.uk"), "w", encoding="utf-8") as f:
            f.write(uk)
        _sub_set(rel, "done", "Hotovo: ukrajinske podle anglickeho casovani.")
    except Exception as e:
        _sub_set(rel, "failed", "Chyba: " + str(e)[:80])


def _normalize_srt(raw):
    """Z libovolneho (i divne formatovaneho) SRT udela cisty standardni SRT."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1250", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out, n = [], 0
    for m in _CUE_RE.finditer(text):
        s = m.group(1) + "," + m.group(2)
        e = m.group(3) + "," + m.group(4)
        lines = [ln.strip() for ln in _ASS_TAG.sub("", m.group(5)).split("\n") if ln.strip()]
        while lines and lines[-1].isdigit():
            lines.pop()
        if not lines:
            continue
        n += 1
        out.append("%d\n%s --> %s\n%s" % (n, s, e, "\n".join(lines)))
    return "\n\n".join(out) + "\n"


_FFSUBSYNC = None


def _ffsubsync_ready():
    """Vrati cestu k ffsubsync (a zpristupni ffmpeg), nebo '' kdyz neni."""
    global _FFSUBSYNC
    if _FFSUBSYNC is not None:
        return _FFSUBSYNC
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception:
        pass
    import shutil
    exe = shutil.which("ffsubsync")
    if not exe:
        cand = os.path.join(os.path.dirname(sys.executable), "Scripts", "ffsubsync.exe")
        exe = cand if os.path.exists(cand) else ""
    _FFSUBSYNC = exe or ""
    return _FFSUBSYNC


def _subsync_worker(rel, src_rel):
    """Srovna zadany titulek na video podle zvuku (ffsubsync). Ulozi *.srovnane.srt."""
    try:
        exe = _ffsubsync_ready()
        if not exe:
            _sub_set(rel, "failed", "Nastroj ffsubsync neni k dispozici.")
            return
        video = os.path.join(MEDIA_ROOT, rel.replace("/", os.sep))
        srcpath = os.path.join(MEDIA_ROOT, src_rel.replace("/", os.sep))
        if not os.path.isfile(video) or not os.path.isfile(srcpath):
            _sub_set(rel, "failed", "Soubor nenalezen.")
            return
        _sub_set(rel, "running", "Pripravuji titulky...")
        with open(srcpath, "rb") as f:
            clean = _normalize_srt(f.read())
        if clean.count(" --> ") < 3:
            _sub_set(rel, "failed", "Titulky se nepodarilo nacist.")
            return
        tag = str(abs(hash(src_rel)) % 1000000)
        tmp_in = os.path.join(tempfile.gettempdir(), "ffs_in_" + tag + ".srt")
        tmp_out = os.path.join(tempfile.gettempdir(), "ffs_out_" + tag + ".srt")
        with open(tmp_in, "w", encoding="utf-8") as f:
            f.write(clean)
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        _sub_set(rel, "running", "Srovnavam titulky podle zvuku filmu (~1-2 min)...")
        r = subprocess.run([exe, video, "-i", tmp_in, "-o", tmp_out],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
            _sub_set(rel, "failed", "Srovnani se nepodarilo (nedostatek reci?).")
            return
        with open(tmp_out, "rb") as f:
            synced = f.read()
        vdir = os.path.dirname(video)
        srcbase = os.path.splitext(os.path.basename(srcpath))[0]
        outpath = os.path.join(vdir, srcbase + ".srovnane.srt")
        i = 2
        while os.path.exists(outpath):
            outpath = os.path.join(vdir, "%s.srovnane%d.srt" % (srcbase, i))
            i += 1
        with open(outpath, "wb") as f:
            f.write(synced)
        for tmp in (tmp_in, tmp_out):
            try:
                os.remove(tmp)
            except OSError:
                pass
        _sub_set(rel, "done", "Hotovo: titulky srovnane presne na video.")
    except Exception as e:
        _sub_set(rel, "failed", "Chyba: " + str(e)[:80])


FINDSUB_JS = """
function findSub(){var s=document.getElementById('substat');if(s){s.style.display='block';s.textContent='Spoustim...';}
 fetch('/subfetch?f='+encodeURIComponent(REL),{cache:'no-store'}).then(pollSub);}
function syncSub(){var s=document.getElementById('substat');if(s){s.style.display='block';s.textContent='Prekladam z anglickych...';}
 fetch('/subtranslate?f='+encodeURIComponent(REL),{cache:'no-store'}).then(pollSub);}
function syncVid(){var s=document.getElementById('substat');var sel=document.getElementById('subsel');
 var idx=sel?parseInt(sel.value,10):-1;
 if(idx<0||!window.SUBRELS||!SUBRELS[idx]){if(s){s.style.display='block';s.textContent='Nejdriv v menu vyber titulek, ktery se ma srovnat.';}return;}
 if(s){s.style.display='block';s.textContent='Spoustim srovnani podle zvuku...';}
 fetch('/subsync?f='+encodeURIComponent(REL)+'&src='+encodeURIComponent(SUBRELS[idx]),{cache:'no-store'}).then(pollSub);}
function pollSub(){fetch('/substatus?f='+encodeURIComponent(REL),{cache:'no-store'}).then(function(r){return r.json();}).then(function(st){
 var s=document.getElementById('substat');if(s)s.textContent=st.msg||'';
 if(st.state==='running'){setTimeout(pollSub,1500);}
 else if(st.state==='done'){if(s)s.textContent=st.msg||'';
   if(window.pollSubs){pollSubs();}else{setTimeout(function(){location.reload();},900);}}
});}
"""

# Zive sledovani slozky: kdyz pribude .srt, doplni se do prehravace bez reloadu.
SUBLIST_JS = """
function subsEqual(a,b){if(a.length!==b.length)return false;for(var i=0;i<a.length;i++){if(a[i].u!==b[i].u)return false;}return true;}
function rebuildSubs(list){
 var sel=document.getElementById('subsel');var vid=document.getElementById('vid');
 var prevUrl=null,anyShow=false;
 if(vid){var tt=vid.textTracks;for(var i=0;i<tt.length;i++){if(tt[i].mode==='showing'&&curSubs[i]){anyShow=true;prevUrl=curSubs[i].u;}}}
 if(vid){var olds=vid.querySelectorAll('track');for(var k=olds.length-1;k>=0;k--)olds[k].remove();}
 if(sel)sel.innerHTML='<option value=\\"-1\\">Titulky vypnuty</option>';
 list.forEach(function(su,idx){
  if(vid){var t=document.createElement('track');t.kind='subtitles';t.srclang='cs';t.label=su.l;t.src=su.u;vid.appendChild(t);}
  if(sel){var o=document.createElement('option');o.value=idx;o.textContent=su.l;sel.appendChild(o);}
 });
 curSubs=list;
 if(sel){var v='-1';if(anyShow){for(var j=0;j<list.length;j++){if(list[j].u===prevUrl){v=String(j);break;}}}
  sel.value=v;if(typeof setSub==='function')setSub(sel.value);}
}
function pollSubs(){fetch('/sublist?f='+encodeURIComponent(REL),{cache:'no-store'}).then(function(r){return r.json();}).then(function(list){if(!subsEqual(list,curSubs))rebuildSubs(list);}).catch(function(){});}
setInterval(pollSubs,4000);
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "FilmyServer/1.0"

    def log_message(self, fmt, *args):
        # kratky log do konzole
        print(f"  {self.address_string()} - {fmt % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html" or path == "/all":
            self.page_all()
        elif path == "/all.m3u":
            self.playlist()
        elif path.startswith("/media/"):
            self.serve_media(path[len("/media/"):])
        elif path.startswith("/vtt/"):
            self.serve_vtt(path[len("/vtt/"):])
        elif path.startswith("/cache/"):
            self.serve_cache(path[len("/cache/"):])
        elif path == "/play":
            self.page_play(parsed.query)
        elif path == "/tv":
            self.send_html(TV_PAGE)
        elif path == "/remote":
            self.page_remote(parsed.query)
        elif path == "/cast/state":
            self.send_json(cast_snapshot(_cast))
        elif path == "/cast/tv":
            self.send_json(cast_snapshot(_tv))
        elif path == "/cast/cmd":
            self.cast_cmd(parsed.query)
        elif path == "/cast/report":
            self.cast_report(parsed.query)
        elif path == "/subfetch":
            self.sub_fetch(parsed.query)
        elif path == "/subtranslate":
            self.sub_translate(parsed.query)
        elif path == "/subsync":
            self.sub_sync(parsed.query)
        elif path == "/substatus":
            self.sub_status(parsed.query)
        elif path == "/sublist":
            self.sub_list(parsed.query)
        else:
            self.send_error(404, "Nenalezeno")

    def sub_list(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        subs = find_subtitles(rel) if (rel and safe_path(rel)) else []
        out = [{"u": "/vtt/" + urllib.parse.quote(s["rel"]), "l": s["label"]}
               for s in subs]
        self.send_json(out)

    # ---------- Dohledani + preklad titulku ----------
    def sub_fetch(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        if not rel or safe_path(rel) is None:
            self.send_json({"ok": False})
            return
        with _subjobs_lock:
            cur = _subjobs.get(rel)
            running = bool(cur and cur.get("state") == "running")
            if not running:
                _subjobs[rel] = {"state": "running", "msg": "Spoustim..."}
        if not running:
            threading.Thread(target=_subfetch_worker, args=(rel,), daemon=True).start()
        self.send_json({"ok": True})

    def sub_translate(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        if not rel or safe_path(rel) is None:
            self.send_json({"ok": False})
            return
        with _subjobs_lock:
            cur = _subjobs.get(rel)
            running = bool(cur and cur.get("state") == "running")
            if not running:
                _subjobs[rel] = {"state": "running", "msg": "Spoustim..."}
        if not running:
            threading.Thread(target=_subtranslate_worker, args=(rel,), daemon=True).start()
        self.send_json({"ok": True})

    def sub_sync(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        src = qs.get("src", [""])[0]
        if (not rel or safe_path(rel) is None
                or not src or safe_path(src) is None):
            self.send_json({"ok": False})
            return
        with _subjobs_lock:
            cur = _subjobs.get(rel)
            running = bool(cur and cur.get("state") == "running")
            if not running:
                _subjobs[rel] = {"state": "running", "msg": "Spoustim..."}
        if not running:
            threading.Thread(target=_subsync_worker, args=(rel, src), daemon=True).start()
        self.send_json({"ok": True})

    def sub_status(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        with _subjobs_lock:
            st = dict(_subjobs.get(rel) or {"state": "idle", "msg": ""})
        st["subs"] = len(find_subtitles(rel)) if rel else 0
        self.send_json(st)

    # ---------- Ovladac (mobil) ----------
    def page_remote(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        if not rel or safe_path(rel) is None:
            self.send_error(404, "Nenalezeno")
            return
        try:
            sub = int(qs.get("sub", ["-1"])[0])
        except ValueError:
            sub = -1
        self.send_html(page_remote_html(rel, sub))

    # ---------- Prikazy z mobilu ----------
    def cast_cmd(self, query):
        qs = urllib.parse.parse_qs(query)
        a = qs.get("a", [""])[0]
        with _cast_lock:
            if a == "play":
                rel = qs.get("f", [""])[0]
                if rel and safe_path(rel):
                    try:
                        sub = int(qs.get("sub", ["-1"])[0])
                    except ValueError:
                        sub = -1
                    _cast["rel"] = rel
                    _cast["url"] = "/media/" + urllib.parse.quote(rel)
                    _cast["title"] = clean_title(rel)[0]
                    _cast["subs"] = [{"u": "/vtt/" + urllib.parse.quote(s["rel"]),
                                      "l": s["label"]} for s in find_subtitles(rel)]
                    _cast["sub"] = sub
                    _cast["paused"] = False
                    _cast["seek"] = 0.0
                    _cast["seekVer"] += 1
                    _cast["ver"] += 1
            elif a == "pause":
                _cast["paused"] = True
                _cast["ver"] += 1
            elif a == "resume":
                _cast["paused"] = False
                _cast["ver"] += 1
            elif a == "stop":
                _cast["rel"] = None
                _cast["url"] = None
                _cast["paused"] = True
                _cast["ver"] += 1
            elif a == "seek":
                try:
                    _cast["seek"] = float(qs.get("t", ["0"])[0])
                    _cast["seekVer"] += 1
                    _cast["ver"] += 1
                except ValueError:
                    pass
            elif a == "sub":
                try:
                    _cast["sub"] = int(qs.get("i", ["-1"])[0])
                    _cast["ver"] += 1
                except ValueError:
                    pass
        self.send_json({"ok": True})

    # ---------- Hlaseni stavu z TV ----------
    def cast_report(self, query):
        qs = urllib.parse.parse_qs(query)
        with _cast_lock:
            try:
                _tv["time"] = float(qs.get("t", ["0"])[0])
            except ValueError:
                pass
            try:
                _tv["dur"] = float(qs.get("d", ["0"])[0])
            except ValueError:
                pass
            _tv["paused"] = qs.get("p", ["1"])[0] == "1"
            _tv["rel"] = qs.get("rel", [None])[0]
        self.send_json({"ok": True})

    def send_json(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ---------- Domu / prochazeni slozky ----------
    def page_home(self, rel):
        if rel:
            sp = safe_path(rel)
            if not sp or not os.path.isdir(sp):
                rel = ""
        folders, videos = list_level(rel)
        grid = ("".join(folder_card_html(n, cr, c) for n, cr, c in folders)
                + "".join(video_card_html(r, s) for r, s in videos))
        if not grid:
            grid = '<p style="opacity:.6;grid-column:1/-1;padding:20px">Prazdna slozka.</p>'
        top = ('<header><div class="crumb">' + _breadcrumb(rel) + '</div>'
               '<input class="search" placeholder="Hledat zde..." '
               'oninput="filter(this.value)" autocomplete="off"></header>')
        if not OMDB_API_KEY:
            top += ('<div class="banner">Tip: pro <b>plakaty a IMDB hodnoceni</b> vloz free OMDb klic '
                    'do souboru filmy_server.py (radek OMDB_API_KEY). Klic zdarma: '
                    '<a href="https://www.omdbapi.com/apikey.aspx">omdbapi.com</a>. '
                    'Zatim se ukazuji barevne dlazdice.</div>')
        count = len(folders) + len(videos)
        bar = ('<div class="bar"><span id="cnt">' + str(count) + ' polozek</span>'
               '<span><a href="/all">Vsechny filmy</a> &nbsp;&middot;&nbsp; '
               '<a href="/all.m3u">&#9654; Do VLC</a></span></div>')
        self.send_html(build_page("FILMY", top, bar, grid))

    # ---------- Vsechny filmy (plocho, globalni hledani) ----------
    def page_all(self):
        vids = list_videos()
        grid = "".join(video_card_html(r, s) for r, s in vids)
        if not grid:
            grid = '<p style="opacity:.6;grid-column:1/-1;padding:20px">Zadne video.</p>'
        top = ('<header><div class="crumb"><a href="/">&#127916; FILMY</a></div>'
               '<input class="search" placeholder="Hledat film..." '
               'oninput="filter(this.value)" autocomplete="off"></header>')
        bar = ('<div class="bar"><span id="cnt">' + str(len(vids)) + ' filmu</span>'
               '<span><a id="tvtoggle" href="javascript:void(0)" onclick="toggleTv()">'
               '&#128250; TV: VYP</a> &nbsp;&middot;&nbsp; '
               '<a href="/all.m3u">&#9654; VLC</a></span></div>')
        self.send_html(build_page("FILMY", top, bar, grid))

    # ---------- Plakat z cache ----------
    def serve_cache(self, name):
        name = urllib.parse.unquote(name)
        if "/" in name or "\\" in name or ".." in name:
            self.send_error(404)
            return
        path = os.path.join(CACHE_DIR, name)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        ctype = "image/png" if data[:8].startswith(b"\x89PNG") else \
                ("image/webp" if data[8:12] == b"WEBP" else "image/jpeg")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=604800")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ---------- Prehravac v prohlizeci ----------
    def page_play(self, query):
        qs = urllib.parse.parse_qs(query)
        rel = qs.get("f", [""])[0]
        if not rel or safe_path(rel) is None:
            self.send_error(404, "Nenalezeno")
            return
        enc = "/media/" + urllib.parse.quote(rel)
        name = html.escape(rel)
        rel_json = json.dumps(rel)
        findsub_js = FINDSUB_JS
        sublist_js = SUBLIST_JS
        subs = find_subtitles(rel)
        subs_json = json.dumps([{"u": "/vtt/" + urllib.parse.quote(s["rel"]),
                                 "l": s["label"]} for s in subs])
        subrels_json = json.dumps([s["rel"] for s in subs])
        tracks = ""
        options = '<option value="-1">Titulky vypnuty</option>'
        for i, s in enumerate(subs):
            venc = "/vtt/" + urllib.parse.quote(s["rel"])
            lbl = html.escape(s["label"])
            default = " default" if i == 0 else ""
            tracks += (f'<track kind="subtitles" src="{venc}" '
                       f'srclang="cs" label="{lbl}"{default}>')
            sel = " selected" if i == 0 else ""
            options += f'<option value="{i}"{sel}>{lbl}</option>'
        subcount = len(subs)
        page = f"""<!doctype html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0c10;color:#e8e8ea;font-family:Segoe UI,system-ui,Arial,sans-serif;-webkit-text-size-adjust:100%}}
.top{{padding:12px 16px;background:#0e0f13;display:flex;align-items:center;gap:12px;border-bottom:1px solid #1c1f2a}}
a{{color:#7db8ff;text-decoration:none}}
video{{width:100%;max-height:68vh;background:#000;display:block}}
video::cue{{background:rgba(0,0,0,.6);color:#fff;font-size:1.05em}}
.panel{{padding:16px;max-width:640px;margin:0 auto}}
.card{{background:#14161d;border:1px solid #1e2230;border-radius:16px;padding:16px}}
.ph{{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:700;margin-bottom:12px}}
.ph .cnt{{margin-left:auto;font-size:12px;font-weight:600;background:#20263a;color:#9fb4d6;padding:3px 10px;border-radius:20px}}
#subsel{{width:100%;padding:14px 12px;font-size:16px;background:#0f1219;color:#fff;border:1px solid #2a2e3d;border-radius:12px}}
#subsel:focus{{outline:none;border-color:#3b82f6}}
.acts{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}}
.act{{display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px 6px;border-radius:14px;border:1px solid #2a2f3e;background:#0f1219;color:#e8e8ea;cursor:pointer;font-size:13.5px;font-weight:600;line-height:1.2;text-align:center}}
.act i{{font-size:22px;font-style:normal;line-height:1}}
.act small{{display:block;font-size:11px;font-weight:400;opacity:.55;margin-top:2px}}
.act:active{{transform:scale(.96)}}
.act.a1{{border-color:#2f6e4a}}
.act.a2{{border-color:#2f4f7e}}
.act.a3{{border-color:#7e5a2f}}
.substat{{display:none;margin-top:12px;padding:10px 12px;font-size:13px;background:#0f1622;border:1px solid #24344a;border-radius:10px;color:#bcd3ee}}
.note{{margin-top:14px;font-size:13px;color:#8a93a6}}
.note summary{{cursor:pointer;list-style:none;padding:8px 0}}
.note summary::-webkit-details-marker{{display:none}}
.nb{{padding:2px 2px 2px;line-height:1.55}}
.url{{font-family:Consolas,monospace;color:#8fd3ff;user-select:all;word-break:break-all}}
</style></head><body>
<div class="top"><a href="/">&larr; Zpet na seznam</a></div>
<video id="vid" controls autoplay crossorigin="anonymous" src="{enc}">{tracks}</video>
<div class="panel"><div class="card">
  <div class="ph">&#128172; Titulky<span class="cnt">{subcount}</span></div>
  <select id="subsel" onchange="setSub(this.value)">{options}</select>
  <div class="acts">
    <button class="act a1" onclick="findSub()"><i>&#127760;</i><span>Dohledat<small>CZ + UK</small></span></button>
    <button class="act a2" onclick="syncSub()"><i>&#128260;</i><span>Prelozit<small>EN&rarr;UA</small></span></button>
    <button class="act a3" onclick="syncVid()"><i>&#127919;</i><span>Srovnat<small>na video</small></span></button>
  </div>
  <div class="substat" id="substat"></div>
  <details class="note"><summary>&#9432; Video se neprehrava? (MKV / HEVC)</summary>
  <div class="nb">Prohlizec neumi <b>MKV/HEVC</b> &ndash; otevri ve <b>VLC</b>:<br>
  <span class="url">http://{get_lan_ip()}:{PORT}{enc}</span></div></details>
</div></div>
<script>
var REL={rel_json};
var curSubs={subs_json};
var SUBRELS={subrels_json};
var vid = document.getElementById('vid');
function setSub(idx){{
  idx = parseInt(idx, 10);
  var tt = vid.textTracks;
  for (var i = 0; i < tt.length; i++){{
    tt[i].mode = (i === idx) ? 'showing' : 'hidden';
  }}
}}
// po nacteni zapni vychozi (prvni = ceske, pokud jsou)
window.addEventListener('load', function(){{
  var sel = document.getElementById('subsel');
  if (sel) setSub(sel.value);
}});
{findsub_js}
{sublist_js}
</script>
</body></html>"""
        self.send_html(page)

    # ---------- Titulky jako WebVTT (pro prohlizec) ----------
    def serve_vtt(self, rel):
        full = safe_path(rel)
        if not full or not os.path.isfile(full):
            self.send_error(404, "Titulky nenalezeny")
            return
        try:
            with open(full, "rb") as f:
                vtt = srt_to_vtt(f.read())
        except OSError:
            self.send_error(404, "Titulky nenalezeny")
            return
        data = vtt.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ---------- M3U playlist ----------
    def playlist(self):
        ip = get_lan_ip()
        base = f"http://{ip}:{PORT}"
        lines = ["#EXTM3U"]
        for rel, size in list_videos():
            title = os.path.splitext(os.path.basename(rel))[0]
            lines.append(f"#EXTINF:-1,{title}")
            sub_rel = find_subtitle(rel)
            if sub_rel:
                sub_url = f"{base}/media/{urllib.parse.quote(sub_rel)}"
                # VLC: pokusi se automaticky nacist externi titulky
                lines.append(f"#EXTVLCOPT:input-slave={sub_url}")
            lines.append(f"{base}/media/{urllib.parse.quote(rel)}")
        data = ("\n".join(lines) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-mpegurl; charset=utf-8")
        self.send_header("Content-Disposition", 'inline; filename="filmy.m3u"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------- Servirovani souboru s podporou Range (seekovani) ----------
    def serve_media(self, rel):
        full = safe_path(rel)
        if not full or not os.path.isfile(full):
            self.send_error(404, "Soubor nenalezen")
            return
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng and rng.startswith("bytes="):
            try:
                s_str, e_str = rng[6:].split("-", 1)
                if s_str:
                    start = int(s_str)
                if e_str:
                    end = int(e_str)
                partial = True
            except ValueError:
                partial = False
                start, end = 0, size - 1
        if start < 0:
            start = 0
        if end >= size:
            end = size - 1
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(full, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # klient (VLC) preskocil/zavrel - normalni, ignoruj
            return

    def do_HEAD(self):
        self.do_GET()

    def send_html(self, page):
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def main():
    if not os.path.isdir(MEDIA_ROOT):
        print(f"CHYBA: slozka {MEDIA_ROOT} neexistuje.")
        input("Enter pro ukonceni...")
        return
    ip = get_lan_ip()
    n = len(list_videos())
    print("=" * 56)
    print("  FILMY server bezi")
    print("=" * 56)
    print(f"  Filmu nalezeno : {n}")
    print(f"  Slozka         : {MEDIA_ROOT}")
    print()
    print("  NA XBOXU (VLC) - hraje vsechny formaty:")
    print(f"     Sit -> Otevrit sitovy proud -> http://{ip}:{PORT}/all.m3u")
    print()
    print("  V PROHLIZECI (PC / mobil / Xbox Edge):")
    print(f"     http://{ip}:{PORT}/")
    print()
    print("  NA TV (prohlizec) - fullscreen prehravac ovladany z mobilu:")
    print(f"     http://{ip}:{PORT}/tv")
    print("     (na mobilu zapni 'TV: ZAP' a pust film)")
    print()
    print("  Plakaty: ZAP - stahuji se na pozadi z Wikipedie (bez klice)")
    if OMDB_API_KEY:
        print("  IMDB hodnoceni: ZAP")
    else:
        print("  IMDB hodnoceni: VYP (nepovinne - OMDb klic v filmy_server.py)")
    print()
    print("  (Server nech bezet. Zavrit = zavri toto okno.)")
    print("=" * 56)
    start_enrichment()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nUkoncuji...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
