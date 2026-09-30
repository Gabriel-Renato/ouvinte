"""Transcrição local de áudio e vídeo com faster-whisper."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("ouvinte")

MEDIA_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".wave",
    ".m4a",
    ".aac",
    ".ogg",
    ".oga",
    ".flac",
    ".opus",
    ".wma",
    ".mpga",
    ".aiff",
    ".aif",
    ".amr",
    ".mp4",
    ".m4v",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".wmv",
    ".mpeg",
    ".mpg",
    ".flv",
    ".3gp",
    ".ts",
    ".m2ts",
}

LANGUAGE_CHOICES: list[tuple[str, str | None]] = [
    ("Português", "pt"),
    ("Automático", None),
    ("Inglês", "en"),
    ("Espanhol", "es"),
    ("Francês", "fr"),
    ("Alemão", "de"),
    ("Italiano", "it"),
    ("Japonês", "ja"),
]

MODEL_CHOICES: list[tuple[str, str, str]] = [
    ("large-v3 — máxima precisão", "large-v3", "Primeiro uso baixa cerca de 3 GB. É o modelo local mais preciso."),
    ("large-v3-turbo — rápido", "large-v3-turbo", "Quase a mesma precisão e bem mais rápido. Cerca de 1,6 GB."),
    ("medium — equilíbrio", "medium", "Boa precisão com menos memória. Cerca de 1,5 GB."),
    ("small — leve", "small", "Mais rápido e menos preciso. Cerca de 500 MB."),
]

LANG_NAMES = {
    "pt": "português",
    "en": "inglês",
    "es": "espanhol",
    "fr": "francês",
    "de": "alemão",
    "it": "italiano",
    "ja": "japonês",
}


class TranscriptionError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    source: Path
    language: str
    language_probability: float
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(segment.text for segment in self.segments).strip()


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) / "Ouvinte" if root else app_dir() / "Ouvinte"
    else:
        base = app_dir() / ".dados"
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def collect_media(paths: list[Path]) -> tuple[list[Path], int]:
    found: list[Path] = []
    skipped = 0
    seen: set[Path] = set()
    for raw in paths:
        path = raw.expanduser()
        if path.is_dir():
            for child in path.rglob("*"):
                if not child.is_file():
                    continue
                if is_media(child):
                    resolved = child.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        found.append(resolved)
                elif child.suffix:
                    skipped += 1
        elif is_media(path):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(resolved)
        else:
            skipped += 1
    return found, skipped


def srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        end = max(segment.end, segment.start + 0.001)
        blocks.append(
            f"{index}\n{srt_timestamp(segment.start)} --> {srt_timestamp(end)}\n{segment.text}\n"
        )
    return "\n".join(blocks)


def language_label(code: str) -> str:
    return LANG_NAMES.get(code, code or "desconhecido")


def _hidden_popen(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)


def read_wav(path: Path):
    import numpy as np
    import wave

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        frame_count = handle.getnframes()
        frames = handle.readframes(frame_count)
    if sample_width != 2 or frame_count <= 0:
        raise TranscriptionError("O arquivo não tem áudio utilizável.")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        usable = (audio.size // channels) * channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)
    if audio.size == 0:
        raise TranscriptionError("O arquivo não tem áudio utilizável.")
    return np.ascontiguousarray(audio, dtype=np.float32)


def extract_wav(source: Path, destination: Path, cancel_event: threading.Event) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    process = _hidden_popen(cmd)
    stderr_parts: list[bytes] = []

    def _read_stderr() -> None:
        if process.stderr is not None:
            stderr_parts.append(process.stderr.read())

    reader = threading.Thread(target=_read_stderr, daemon=True)
    reader.start()
    while process.poll() is None:
        if cancel_event.is_set():
            process.terminate()
            reader.join(timeout=3)
            raise Cancelled()
        time.sleep(0.2)
    reader.join(timeout=5)
    if process.returncode != 0:
        detail = b"".join(stderr_parts).decode("utf-8", errors="replace").strip()
        message = "Não foi possível ler o áudio deste arquivo."
        if detail:
            message = f"{message} {detail[:400]}"
        raise TranscriptionError(message)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


class Transcriber:
    def __init__(self) -> None:
        self._model = None
        self._model_name: str | None = None
        self._device = ""
        self._compute = ""
        self._lock = threading.Lock()

    @property
    def device_label(self) -> str:
        if self._device == "cuda":
            return "GPU"
        if self._device == "cpu":
            return "CPU"
        return ""

    def _load(self, model_name: str):
        if self._model is not None and self._model_name == model_name:
            return self._model

        from faster_whisper import WhisperModel

        download_root = str(data_dir() / "modelos")
        Path(download_root).mkdir(parents=True, exist_ok=True)
        attempts: list[tuple[str, str]] = []
        if _cuda_available():
            attempts.append(("cuda", "float16"))
            attempts.append(("cuda", "int8_float16"))
        attempts.append(("cpu", "int8"))

        last_error: Exception | None = None
        for device, compute_type in attempts:
            try:
                log.info("Carregando %s em %s/%s", model_name, device, compute_type)
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    download_root=download_root,
                    cpu_threads=os.cpu_count() or 4,
                )
                self._model = model
                self._model_name = model_name
                self._device = device
                self._compute = compute_type
                return model
            except Exception as exc:
                last_error = exc
                log.exception("Falha ao carregar modelo em %s/%s", device, compute_type)
                self._model = None

        raise TranscriptionError(
            "Não foi possível carregar o modelo de transcrição. "
            "Na primeira vez é preciso de internet para baixar o modelo. "
            f"Detalhe: {last_error}"
        ) from last_error

    def transcribe(
        self,
        source: Path,
        *,
        model_name: str,
        language: str | None,
        hotwords: str,
        cancel_event: threading.Event,
        on_status,
        on_progress,
    ) -> Transcript:
        if cancel_event.is_set():
            raise Cancelled()
        if not source.is_file():
            raise TranscriptionError(f"Arquivo não encontrado: {source}")

        on_status(f"Preparando áudio de {source.name}...")
        on_progress(0.02)

        with tempfile.TemporaryDirectory(prefix="ouvinte-") as temp_dir:
            wav_path = Path(temp_dir) / "audio.wav"
            extract_wav(source, wav_path, cancel_event)
            audio = read_wav(wav_path)

            if cancel_event.is_set():
                raise Cancelled()

            on_status(self._loading_message(model_name))
            with self._lock:
                model = self._load(model_name)

            on_status(f"Transcrevendo {source.name} ({self.device_label})...")
            kwargs = self._transcribe_kwargs(language, hotwords)
            segments_iter, info = self._call_transcribe(model, audio, kwargs)
            duration = float(getattr(info, "duration", 0) or 0)
            transcript = Transcript(
                source=source,
                language=getattr(info, "language", language or "") or "",
                language_probability=float(getattr(info, "language_probability", 0) or 0),
            )

            for segment in segments_iter:
                if cancel_event.is_set():
                    raise Cancelled()
                text = (segment.text or "").strip()
                if not text:
                    continue
                transcript.segments.append(Segment(float(segment.start), float(segment.end), text))
                if duration > 0:
                    on_progress(min(0.99, max(0.05, float(segment.end) / duration)))

            on_progress(1.0)
            return transcript

    def _loading_message(self, model_name: str) -> str:
        if self._model is not None and self._model_name == model_name:
            return f"Modelo {model_name} pronto."
        return (
            f"Carregando o modelo {model_name}. "
            "Na primeira vez ele é baixado e isso pode demorar."
        )

    def _transcribe_kwargs(self, language: str | None, hotwords: str) -> dict:
        kwargs: dict = {
            "beam_size": 5,
            "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            "vad_filter": True,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            "hallucination_silence_threshold": 2.0,
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
        }
        if language:
            kwargs["language"] = language
        words = " ".join(hotwords.split())
        if words:
            kwargs["hotwords"] = words
        try:
            from faster_whisper.vad import VadOptions

            kwargs["vad_parameters"] = VadOptions(min_silence_duration_ms=500)
        except Exception:
            log.debug("VadOptions indisponível; usando o filtro padrão.")
        return kwargs

    def _call_transcribe(self, model, audio, kwargs: dict):
        try:
            return model.transcribe(audio, **kwargs)
        except TypeError as exc:
            message = str(exc)
            reduced = dict(kwargs)
            changed = False
            for key in ("hotwords", "hallucination_silence_threshold", "word_timestamps"):
                if key in message and key in reduced:
                    reduced.pop(key)
                    changed = True
            if not changed:
                raise
            log.warning("Repetindo transcrição sem opções não suportadas: %s", message)
            return model.transcribe(audio, **reduced)
