"""
Core clip-generation pipeline for the MVP.

Flow for one uploaded long-form video:
  1. find_highlight_windows() - locate N loudest/most active windows (proxy for "interesting" moments)
  2. crop_and_watermark() - ffmpeg: crop to 9:16, overlay watermark top-left at low opacity
  3. transcribe_to_srt() - faster-whisper transcription -> .srt file
  4. burn_captions() - ffmpeg: burn the .srt onto the clip
  5. process_video() - orchestrates all of the above for every window

This is intentionally built on ffmpeg subprocess calls (not moviepy) for speed and
fewer system dependencies (no ImageMagick needed) - important for a lean Codespaces setup.
"""
import subprocess
import wave
import audioop
import struct
import os
import uuid
from dataclasses import dataclass

from faster_whisper import WhisperModel

# Load once at import time. "tiny" model = fast + free to run on CPU (Codespaces has no GPU).
# Swap to "small" or "base" later for better accuracy once you're validating paid usage.
_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


@dataclass
class HighlightWindow:
    start: float
    end: float
    score: float


def _get_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def find_highlight_windows(video_path: str, window_len: float = 30.0, num_clips: int = 3) -> list[HighlightWindow]:
    """
    Extracts mono 16kHz audio, computes short-term energy (RMS) per window,
    and returns the top `num_clips` non-overlapping windows by average energy.
    This is a fast, dependency-light proxy for "exciting moment detection" -
    good enough for an MVP; can be swapped for a real scene-detection model later.
    """
    duration = _get_duration(video_path)
    tmp_wav = f"/tmp/{uuid.uuid4().hex}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-ac", "1", "-ar", "16000",
         "-vn", tmp_wav],
        capture_output=True, check=True,
    )

    with wave.open(tmp_wav, "rb") as wf:
        framerate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    os.remove(tmp_wav)

    samples_per_window = int(window_len * framerate) * sampwidth
    scores = []
    step = samples_per_window // 2  # 50% stride so we don't miss peaks at boundaries
    pos = 0
    while pos + samples_per_window <= len(raw):
        chunk = raw[pos: pos + samples_per_window]
        rms = audioop.rms(chunk, sampwidth)
        start_sec = pos / (framerate * sampwidth)
        scores.append(HighlightWindow(start=start_sec, end=start_sec + window_len, score=rms))
        pos += step

    if not scores:
        # video shorter than one window - just use the whole thing
        return [HighlightWindow(start=0, end=duration, score=1.0)]

    scores.sort(key=lambda w: w.score, reverse=True)

    # greedily pick top windows that don't overlap
    picked: list[HighlightWindow] = []
    for w in scores:
        if all(w.end <= p.start or w.start >= p.end for p in picked):
            picked.append(w)
        if len(picked) == num_clips:
            break

    picked.sort(key=lambda w: w.start)
    return picked


def crop_and_watermark(video_path: str, start: float, end: float, out_path: str,
                        watermark_text: str = "@yourhandle") -> None:
    """
    Cuts [start, end], center-crops to 9:16, and burns a low-opacity watermark
    in the TOP-LEFT corner (not center) - avoids the reach-suppression issue
    you saw on Instagram with center watermarks.
    """
    duration = end - start
    vf = (
        "crop=ih*9/16:ih,"  # center-crop to 9:16 based on source height
        "scale=1080:1920,"
        f"drawtext=text='{watermark_text}':x=24:y=24:fontsize=28:"
        "fontcolor=white@0.55:box=1:boxcolor=black@0.25:boxborderw=8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", video_path,
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", out_path],
        capture_output=True, check=True,
    )


def _format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def transcribe_to_srt(clip_path: str, srt_path: str) -> None:
    model = get_whisper_model()
    segments, _ = model.transcribe(clip_path, beam_size=1)
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{_format_srt_timestamp(seg.start)} --> {_format_srt_timestamp(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")


def burn_captions(clip_path: str, srt_path: str, out_path: str) -> None:
    # force_style keeps captions readable on mobile: bold, bottom-third, white w/ outline
    style = "FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=60"
    subprocess.run(
        ["ffmpeg", "-y", "-i", clip_path,
         "-vf", f"subtitles={srt_path}:force_style='{style}'",
         "-c:a", "copy", out_path],
        capture_output=True, check=True,
    )


def process_video(video_path: str, out_dir: str, num_clips: int = 3,
                   watermark_text: str = "@yourhandle") -> list[str]:
    """
    End-to-end: long video in -> N ready-to-post vertical clips out.
    Returns list of output file paths.
    """
    os.makedirs(out_dir, exist_ok=True)
    windows = find_highlight_windows(video_path, num_clips=num_clips)

    outputs = []
    for i, w in enumerate(windows, start=1):
        job_id = uuid.uuid4().hex[:8]
        raw_clip = os.path.join(out_dir, f"clip_{i}_{job_id}_raw.mp4")
        srt_path = os.path.join(out_dir, f"clip_{i}_{job_id}.srt")
        final_clip = os.path.join(out_dir, f"clip_{i}_{job_id}.mp4")

        crop_and_watermark(video_path, w.start, w.end, raw_clip, watermark_text)
        transcribe_to_srt(raw_clip, srt_path)
        burn_captions(raw_clip, srt_path, final_clip)

        os.remove(raw_clip)
        outputs.append(final_clip)

    return outputs
