"""Local audio-to-subtitle worker reused from PZ_AI_DAB_ALL.

The module is deliberately independent from the HTTP server.  Heavy AI imports
and model loading happen only when the normal subtitle sources cannot provide a
reliably timed result.
"""
from __future__ import annotations

import os
import re
import site
import threading
from pathlib import Path
from typing import Callable, Optional


_model_cache = {}
_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()  # one GPU transcription at a time
_TAG_RE = re.compile(r"<[^>\s]{1,32}>")
_dll_handles = []


def _add_cuda_dll_directories() -> None:
    """Make NVIDIA pip-package DLLs visible to CTranslate2 on Windows."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    candidates = []
    for root in site.getsitepackages() + [site.getusersitepackages()]:
        base = Path(root)
        candidates.extend(base.glob("nvidia/*/bin"))
        candidates.append(base / "ctranslate2")
    known = {str(getattr(handle, "path", "")) for handle in _dll_handles}
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    for path in candidates:
        value = str(path)
        if path.is_dir() and value not in known:
            try:
                _dll_handles.append(os.add_dll_directory(value))
                known.add(value)
            except OSError:
                pass
        # CTranslate2 resolves CUDA libraries with LoadLibrary; on some Windows
        # builds that follows PATH even when add_dll_directory was registered.
        if path.is_dir() and value not in path_parts:
            path_parts.insert(0, value)
    os.environ["PATH"] = os.pathsep.join(path_parts)


def _clean(text: str) -> str:
    text = _TAG_RE.sub(" ", text or "")
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _group_words(words: list[dict]) -> list[dict]:
    """Convert Whisper word timestamps into short, TV-readable cues."""
    max_chars = int(os.environ.get("FILMY_AI_MAX_CHARS", "64"))
    max_duration = float(os.environ.get("FILMY_AI_MAX_DURATION", "6"))
    max_gap = float(os.environ.get("FILMY_AI_MAX_GAP", "1"))
    result, current = [], []
    current_start: Optional[float] = None
    current_end = 0.0
    last_end: Optional[float] = None

    def flush() -> None:
        nonlocal current, current_start
        if current:
            text = _clean(" ".join(item["word"] for item in current))
            if text:
                result.append({
                    "start": round(current_start or 0.0, 3),
                    "end": round(max(current_end, (current_start or 0.0) + 0.35), 3),
                    "text": text,
                })
        current = []
        current_start = None

    for item in words:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        start = float(item.get("start", current_end))
        end = float(item.get("end", start + 0.1))
        gap = start - last_end if last_end is not None else 0.0
        candidate = " ".join([w["word"] for w in current] + [word])
        if current and (len(candidate) > max_chars
                        or end - (current_start or 0.0) > max_duration
                        or gap > max_gap):
            flush()
        if current_start is None:
            current_start = start
        current.append({"word": word, "start": start, "end": end})
        current_end = end
        last_end = end
        if word.endswith((".", "!", "?", "…")) and len(candidate) >= max_chars // 2:
            flush()
    flush()
    return result


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _to_srt(segments: list[dict]) -> str:
    blocks = []
    for number, segment in enumerate(segments, 1):
        text = _clean(segment.get("text", ""))
        if not text:
            continue
        blocks.append(
            f"{number}\n{_srt_time(float(segment['start']))} --> "
            f"{_srt_time(float(segment['end']))}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _get_model(model_name: str, device: str, compute_type: str,
               status: Callable[[str], None]):
    _add_cuda_dll_directories()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("chybi faster-whisper (spust instalaci AI titulku)") from exc

    key = (model_name, device, compute_type)
    with _model_lock:
        if key not in _model_cache:
            status("AI: nacitam model %s na %s (poprve se muze stahovat)..." %
                   (model_name, "grafice" if device == "cuda" else "procesoru"))
            _model_cache[key] = WhisperModel(
                model_name, device=device, compute_type=compute_type)
        return _model_cache[key]


def transcribe_english(video_path: str, duration: float = 0.0,
                       status: Optional[Callable[[str], None]] = None) -> tuple[str, dict]:
    """Transcribe English speech and return (SRT, diagnostic metadata)."""
    status = status or (lambda _message: None)
    # Medium is already used by PZ_AI_DAB_ALL and is a good accuracy/speed
    # compromise on the local RTX 4070 Ti. It can be overridden by env var.
    model_name = os.environ.get("FILMY_WHISPER_MODEL", "medium")
    device = os.environ.get("FILMY_WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get(
        "FILMY_WHISPER_COMPUTE", "float16" if device == "cuda" else "int8")

    with _transcribe_lock:
        model = _get_model(model_name, device, compute_type, status)
        status("AI: posloucham anglicky zvuk a vytvarim presne casy...")
        segments_iter, info = model.transcribe(
            video_path,
            language="en",
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
        words = []
        raw_segments = 0
        last_reported = -1
        for segment in segments_iter:
            raw_segments += 1
            for word in segment.words or []:
                value = (word.word or "").strip()
                if value and word.start is not None and word.end is not None:
                    words.append({
                        "word": value,
                        "start": float(word.start),
                        "end": float(word.end),
                        "probability": float(word.probability or 0.0),
                    })
            if duration > 0:
                percent = min(99, int(float(segment.end) * 100 / duration))
                if percent >= last_reported + 5:
                    status("AI: prepisuji zvuk... %d %%" % percent)
                    last_reported = percent

    cues = _group_words(words)
    if len(cues) < 2 or len(words) < 5:
        raise RuntimeError("ve zvuku nebylo nalezeno dost anglicke reci")
    srt = _to_srt(cues)
    return srt, {
        "model": model_name,
        "device": device,
        "detected_language": getattr(info, "language", "en"),
        "words": len(words),
        "cues": len(cues),
        "raw_segments": raw_segments,
    }
