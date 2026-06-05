#!/usr/bin/env python3
"""Build web-optimized rollout videos for the UMI-Bench project page."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(
    "/Users/yuntian.wang/Desktop/workspace/latex/UMI_Bench/videos/video_rollout"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "assets" / "videos" / "rollouts"
LOCAL_FFMPEG_FALLBACK = Path(
    "/Users/yuntian.wang/.local/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1"
)

TASK_RE = re.compile(r"task\s*([0-9]+)", re.IGNORECASE)
SPLITS = ("Seen", "Unseen")
SAMPLE_ITEMS = ((1, "Seen"), (5, "Seen"))


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        if LOCAL_FFMPEG_FALLBACK.exists():
            return str(LOCAL_FFMPEG_FALLBACK)
        raise SystemExit(
            "imageio_ffmpeg is required, or provide the local ffmpeg fallback binary."
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def parse_video_info(ffmpeg: str, path: Path) -> dict:
    proc = subprocess.run(
        [ffmpeg, "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    text = proc.stderr
    duration = None
    duration_match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if duration_match:
        h, m, s = duration_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    video_match = re.search(
        r"Video:\s*([^,\n]+).*?,\s*(\d+)x(\d+)(?:[,\s].*?(?:(\d+(?:\.\d+)?)\s*fps))?",
        text,
        re.S,
    )
    width = height = fps = codec = None
    if video_match:
        codec = video_match.group(1).strip()
        width = int(video_match.group(2))
        height = int(video_match.group(3))
        fps = float(video_match.group(4)) if video_match.group(4) else None

    has_audio = "Audio:" in text
    size_bytes = path.stat().st_size if path.exists() else None
    bitrate_mbps = None
    if duration and size_bytes:
        bitrate_mbps = size_bytes * 8 / duration / 1_000_000

    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "duration_s": round(duration, 3) if duration else None,
        "width": width,
        "height": height,
        "fps": fps,
        "codec": codec,
        "has_audio": has_audio,
        "bitrate_mbps": round(bitrate_mbps, 3) if bitrate_mbps else None,
    }


def discover_tasks(source_root: Path) -> dict[int, Path]:
    if not source_root.exists():
        raise SystemExit(f"Source root does not exist: {source_root}")

    tasks: dict[int, Path] = {}
    for child in source_root.iterdir():
        if not child.is_dir():
            continue
        match = TASK_RE.search(child.name)
        if not match:
            continue
        task_id = int(match.group(1))
        if task_id in tasks:
            raise SystemExit(f"Duplicate task{task_id} folders: {tasks[task_id]} and {child}")
        tasks[task_id] = child

    missing = [task_id for task_id in range(1, 11) if task_id not in tasks]
    if missing:
        raise SystemExit(f"Missing task folders: {missing}")
    return tasks


def find_split_file(task_dir: Path, split: str) -> Path:
    candidates = [p for p in task_dir.iterdir() if p.is_file() and p.stem.lower() == split.lower()]
    candidates = [p for p in candidates if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}]
    if len(candidates) != 1:
        raise SystemExit(f"Expected one {split} video in {task_dir}, found {len(candidates)}")
    return candidates[0]


def target_size(task_id: int) -> tuple[int, int]:
    return (640, 640) if task_id <= 4 else (960, 480)


def output_name(task_id: int, split: str) -> str:
    return f"task-{task_id:02d}-{split.lower()}.mp4"


def build_plan(source_root: Path, output_root: Path, sample: bool) -> list[dict]:
    tasks = discover_tasks(source_root)
    requested = SAMPLE_ITEMS if sample else tuple(
        (task_id, split) for task_id in range(1, 11) for split in SPLITS
    )

    items = []
    for task_id, split in requested:
        task_dir = tasks[task_id]
        width, height = target_size(task_id)
        source = find_split_file(task_dir, split)
        items.append(
            {
                "task_id": task_id,
                "task_dir": task_dir.name,
                "split": split,
                "kind": "single_arm" if task_id <= 4 else "bimanual",
                "source": source,
                "output": output_root / output_name(task_id, split),
                "target_width": width,
                "target_height": height,
            }
        )
    return items


def transcode(ffmpeg: str, item: dict, crf: int, preset: str, force: bool, dry_run: bool) -> dict:
    output = item["output"]
    tmp_output = output.with_suffix(".tmp.mp4")
    if output.exists() and not force:
        raise SystemExit(f"Output exists; pass --force to overwrite: {output}")

    vf = f"setpts=0.5*PTS,scale={item['target_width']}:{item['target_height']}:flags=lanczos"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(item["source"]),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(tmp_output),
    ]

    source_info = parse_video_info(ffmpeg, item["source"])
    result = {
        "task_id": item["task_id"],
        "task_dir": item["task_dir"],
        "split": item["split"],
        "kind": item["kind"],
        "source": str(item["source"]),
        "output": str(output),
        "target_width": item["target_width"],
        "target_height": item["target_height"],
        "speed": "2x",
        "crf": crf,
        "preset": preset,
        "source_info": source_info,
        "command": cmd,
    }
    if dry_run:
        result["status"] = "dry_run"
        return result

    output.parent.mkdir(parents=True, exist_ok=True)
    if tmp_output.exists():
        tmp_output.unlink()
    subprocess.run(cmd, check=True)
    os.replace(tmp_output, output)
    result["output_info"] = parse_video_info(ffmpeg, output)
    result["status"] = "ok"
    return result


def write_manifest(output_root: Path, records: list[dict], args: argparse.Namespace) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(args.source_root),
        "output_root": str(args.output_root),
        "sample": args.sample,
        "encoding": {
            "speed": "2x",
            "single_arm_size": "640x640",
            "bimanual_size": "960x480",
            "codec": "libx264",
            "crf": args.crf,
            "preset": args.preset,
            "pix_fmt": "yuv420p",
            "audio": "removed",
            "faststart": True,
        },
        "videos": records,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--crf", type=int, default=28)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--sample", action="store_true", help="Only build task-01 seen and task-05 seen.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ffmpeg = get_ffmpeg()
    items = build_plan(args.source_root, args.output_root, args.sample)
    records = []
    for item in items:
        print(f"[build] task-{item['task_id']:02d} {item['split']} -> {item['output'].name}")
        records.append(transcode(ffmpeg, item, args.crf, args.preset, args.force, args.dry_run))
    if not args.dry_run:
        write_manifest(args.output_root, records, args)

    total_size = sum(
        record.get("output_info", {}).get("size_bytes") or 0 for record in records
    )
    if total_size:
        print(f"[done] {len(records)} videos, {total_size / 1024 / 1024:.1f} MB")
    else:
        print(f"[done] {len(records)} planned videos")


if __name__ == "__main__":
    main()
