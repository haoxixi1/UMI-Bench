#!/usr/bin/env python3
"""Build the UMI-Bench hero mosaic video.

The script selects 144 ten-second clips from the downloaded task videos,
normalizes them into 180x180 silent tiles, builds two 12x6 mosaic segments,
and concatenates them into assets/videos/hero-montage.mp4.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
DEFAULT_FFMPEG = Path(
    "/Users/yuntian.wang/.local/lib/python3.13/site-packages/"
    "imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1"
)


@dataclass(frozen=True)
class Candidate:
    source: str
    task: str
    kind: str
    start: float
    duration: float


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"[ffmpeg] {Path(cmd[-1]).name}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        if DEFAULT_FFMPEG.exists():
            return str(DEFAULT_FFMPEG)
        raise RuntimeError(
            "Could not find ffmpeg. Install imageio_ffmpeg or pass --ffmpeg."
        )


def probe_duration(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not match:
        raise RuntimeError(f"Could not read duration for {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def classify_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "left_hand.mp4":
        return "left_hand"
    if name == "right_hand.mp4":
        return "right_hand"
    if name == "video.mp4":
        return "single_view"
    return path.stem.lower()


def collect_candidates(ffmpeg: str, input_dir: Path, clip_seconds: int) -> list[Candidate]:
    videos = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    candidates: list[Candidate] = []
    for path in videos:
        duration = probe_duration(ffmpeg, path)
        full_windows = int(math.floor(duration / clip_seconds))
        task = path.parent.name
        kind = classify_kind(path)
        for idx in range(full_windows):
            candidates.append(
                Candidate(
                    source=str(path),
                    task=task,
                    kind=kind,
                    start=float(idx * clip_seconds),
                    duration=clip_seconds,
                )
            )
    return candidates


def select_candidates(
    candidates: list[Candidate],
    total: int,
    seed: int,
) -> list[Candidate]:
    rng = random.Random(seed)
    by_task: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_task.setdefault(candidate.task, []).append(candidate)

    for task_candidates in by_task.values():
        rng.shuffle(task_candidates)

    selected: list[Candidate] = []
    task_names = sorted(by_task)
    while len(selected) < total:
        active = [task for task in task_names if by_task[task]]
        if not active:
            break
        rng.shuffle(active)
        for task in active:
            if len(selected) >= total:
                break
            selected.append(by_task[task].pop())

    if len(selected) < total:
        raise RuntimeError(f"Only found {len(selected)} usable 10s clips, need {total}")

    rng.shuffle(selected)
    return selected


def normalize_clip(
    ffmpeg: str,
    candidate: Candidate,
    output: Path,
    tile_size: int,
    fps: int,
    overwrite: bool,
) -> None:
    if output.exists() and not overwrite:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={tile_size}:{tile_size}:force_original_aspect_ratio=increase,"
        f"crop={tile_size}:{tile_size},setsar=1,fps={fps},"
        "tpad=stop_mode=clone:stop_duration=10,trim=duration=10,setpts=PTS-STARTPTS"
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{candidate.start:.3f}",
        "-t",
        f"{candidate.duration:.3f}",
        "-i",
        candidate.source,
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "32",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    run(cmd)


def build_segment(
    ffmpeg: str,
    clips: list[Path],
    output: Path,
    cols: int,
    rows: int,
    tile_size: int,
    clip_seconds: int,
    fade_seconds: float,
    crf: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for clip in clips:
        cmd.extend(["-i", str(clip)])

    filter_inputs = "".join(f"[{idx}:v]" for idx in range(len(clips)))
    layout = "|".join(
        f"{(idx % cols) * tile_size}_{(idx // cols) * tile_size}"
        for idx in range(len(clips))
    )
    filter_complex = (
        f"{filter_inputs}xstack=inputs={len(clips)}:layout={layout}:fill=black,"
        "format=yuv420p,"
        f"fade=t=in:st=0:d={fade_seconds:.3f},"
        f"fade=t=out:st={max(0, clip_seconds - fade_seconds - 0.2):.3f}:d={fade_seconds:.3f}"
        "[outv]"
    )
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    run(cmd)


def concat_segments(ffmpeg: str, segments: list[Path], output: Path, concat_list: Path) -> None:
    concat_list.write_text(
        "".join(f"file '{segment.as_posix()}'\n" for segment in segments),
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("/Users/yuntian.wang/Desktop/workspace/latex/UMI_Bench/random1_many_tasks_folder_20260603_033850/tasks"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/Users/yuntian.wang/Desktop/workspace/latex/UMI_Bench_website/assets/videos/hero-montage.mp4"),
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("/Users/yuntian.wang/Desktop/workspace/latex/UMI_Bench/work/hero_montage"),
    )
    parser.add_argument("--cols", type=int, default=12)
    parser.add_argument("--rows", type=int, default=6)
    parser.add_argument("--tile-size", type=int, default=180)
    parser.add_argument("--clip-seconds", type=int, default=10)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20260604)
    parser.add_argument("--crf", type=int, default=30)
    parser.add_argument("--fade-seconds", type=float, default=0.65)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--overwrite-clips", action="store_true")
    args = parser.parse_args()

    ffmpeg = str(args.ffmpeg) if args.ffmpeg else get_ffmpeg()
    clips_per_segment = args.cols * args.rows
    total_clips = clips_per_segment * 2

    candidates = collect_candidates(ffmpeg, args.input_dir, args.clip_seconds)
    selected = select_candidates(candidates, total_clips, args.seed)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output.with_name("hero-montage-selection.json")
    manifest = {
        "input_dir": str(args.input_dir),
        "output": str(args.output),
        "seed": args.seed,
        "cols": args.cols,
        "rows": args.rows,
        "tile_size": args.tile_size,
        "fps": args.fps,
        "clip_seconds": args.clip_seconds,
        "fade_seconds": args.fade_seconds,
        "selected": [asdict(item) for item in selected],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    normalized_paths: list[Path] = []
    normalized_dir = args.work_dir / "normalized"
    for idx, candidate in enumerate(selected):
        clip_path = normalized_dir / f"clip_{idx:03d}.mp4"
        if clip_path.exists() and not args.overwrite_clips:
            normalized_paths.append(clip_path)
            continue
        print(
            f"[normalize] {idx + 1:03d}/{total_clips} "
            f"{candidate.task}/{Path(candidate.source).name} @ {candidate.start:.0f}s",
            flush=True,
        )
        normalize_clip(
            ffmpeg,
            candidate,
            clip_path,
            args.tile_size,
            args.fps,
            args.overwrite_clips,
        )
        normalized_paths.append(clip_path)

    segment_paths = [
        args.work_dir / "segment_01.mp4",
        args.work_dir / "segment_02.mp4",
    ]
    build_segment(
        ffmpeg,
        normalized_paths[:clips_per_segment],
        segment_paths[0],
        args.cols,
        args.rows,
        args.tile_size,
        args.clip_seconds,
        args.fade_seconds,
        args.crf,
    )
    build_segment(
        ffmpeg,
        normalized_paths[clips_per_segment:],
        segment_paths[1],
        args.cols,
        args.rows,
        args.tile_size,
        args.clip_seconds,
        args.fade_seconds,
        args.crf,
    )
    concat_segments(ffmpeg, segment_paths, args.output, args.work_dir / "concat.txt")


if __name__ == "__main__":
    main()
