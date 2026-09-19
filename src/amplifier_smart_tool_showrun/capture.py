"""Playwright/CDP viewport capture with explicit compositor-frame timebase.

Unlike estimating the hidden origin of Playwright's WebM recorder, timestamps
come directly from Chrome's screencast frames. ffmpeg preserves their intervals;
unchanged frames are held, including all model deliberation and application waits.
"""

import asyncio
import base64
import hashlib
import json
import math
import shutil
import time
from pathlib import Path

from .errors import ShowrunError, require


async def command(*args, timeout=30):
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        require(proc.returncode == 0, "Media processing failed.", "media_invalid")
        return output
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def preflight():
    require(shutil.which("ffmpeg") and shutil.which("ffprobe"),
            "Install ffmpeg and ffprobe before recording.", "capture_dependency_missing")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ShowrunError("capture_dependency_missing", "Install Showrun's Playwright dependency.",
                           "Reinstall Showrun, then run python -m playwright install chromium.") from None
    encoders = await command("ffmpeg", "-v", "error", "-encoders", timeout=5)
    require(b"libx264" in encoders, "Install FFmpeg with its libx264 encoder.", "capture_dependency_missing")
    async with async_playwright() as pw:
        require(Path(pw.chromium.executable_path).is_file(),
                "Install Chromium with this environment's python -m playwright install chromium.",
                "capture_dependency_missing")


async def inspect_media(path):
    result = json.loads(await command("ffprobe", "-v", "error", "-show_streams", "-show_format",
                                      "-of", "json", str(path)))
    streams = [s for s in result["streams"] if s["codec_type"] == "video"]
    require(len(streams) == 1, "Expected one video stream.", "media_invalid")
    stream = streams[0]
    await command("ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-", timeout=30)
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "container": result["format"]["format_name"],
            "width": stream["width"], "height": stream["height"],
            "duration_seconds": float(result["format"]["duration"]),
            "display_aspect_ratio": stream.get("display_aspect_ratio"),
            "sample_aspect_ratio": stream.get("sample_aspect_ratio"), "audio": "none", "decoded": True}


def validate_interval(row, duration):
    """Check receipt consistency, NOT whether its pixels depict the intended state."""
    def check(value):
        require(value, "Step interval disagrees with media, hold or action evidence.", "capture_timing")

    def stamp(value):
        check(type(value) in {int, float} and math.isfinite(value) and value >= 0)
        return value

    try:
        interval, hold = row["interval"], row["hold"]
        start, end = stamp(interval["start_seconds"]), stamp(interval["end_seconds"])
        hs, he = stamp(hold["start_seconds"]), stamp(hold["end_seconds"])
        visible, ended = stamp(row["visible_result_seconds"]), stamp(row["ended_seconds"])
        started = stamp(row["started_seconds"])
        check(interval["precision_seconds"] == .08 and math.isfinite(duration))
        check(started <= start <= hs <= he == ended == end <= duration + .08)
        check(hs == visible and he - hs >= hold["requested_seconds"])
        interactions = [e for e in row["events"] if e["kind"] == "interaction"
                        and e.get("state") != "not_dispatched"]
        first = row["first_interaction_seconds"]
        check(first == (interactions[0]["dispatch_seconds"] if interactions else None))
        check(start == (first if first is not None else visible))
        check(interval["start_basis"] == ("first UI interaction" if first is not None
                                         else "visible result observation; no UI interaction needed"))
        for event in row["events"]:
            if event.get("state") == "not_dispatched":
                check("dispatch_seconds" not in event)
                continue
            if "dispatch_seconds" in event:
                ds, returned = stamp(event["dispatch_seconds"]), stamp(event["returned_seconds"])
                check(event["state"] == "returned" and started <= ds <= returned <= visible)
                if event["kind"] == "interaction":
                    check(start <= ds <= returned <= end)
            elif "start_seconds" in event:
                es, ee = stamp(event["start_seconds"]), stamp(event["end_seconds"])
                # Deliberation may precede the first UI interaction, but remains
                # within the step's recorded lifetime and before the result hold.
                check(started <= es <= ee <= visible)
    except (KeyError, TypeError, ValueError):
        raise ShowrunError("capture_timing", "Step timeline fields are missing or invalid.") from None


class Capture:
    def __init__(self, folder, geometry):
        self.folder, self.geometry = folder, geometry
        self.frames = []
        self.tasks = set()
        self.bytes = 0
        self.error = None
        self.cdp = None
        self.origin = None
        self.epoch_offset = time.time() - time.monotonic()
        self.accepting = True

    def now(self):
        require(self.origin is not None, "Capture has not produced its first frame.", "capture_not_ready")
        return round(time.monotonic() + self.epoch_offset - self.origin, 6)

    async def start(self, context, page):
        self.frame_dir = self.folder / "frames"
        self.frame_dir.mkdir(mode=0o700)
        self.cdp = await context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        await self.cdp.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1,
                                                   "maxWidth": self.geometry["width"],
                                                   "maxHeight": self.geometry["height"]})
        for _ in range(100):
            if self.frames:
                return
            await asyncio.sleep(.05)
        raise ShowrunError("capture_not_ready", "No compositor frame arrived before navigation.")

    def _frame(self, event):
        task = asyncio.create_task(self._save_frame(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _save_frame(self, event):
        try:
            if self.accepting:
                stamp = event["metadata"]["timestamp"]
                data = base64.b64decode(event["data"])
                self.bytes += len(data)
                require(self.bytes <= 512 * 1024 * 1024, "Capture reached its 512 MiB frame limit.", "storage_limit")
                if self.origin is None:
                    self.origin = stamp
                require(not self.frames or stamp >= self.frames[-1][0], "Nonmonotonic capture.", "capture_invalid")
                path = self.frame_dir / f"{len(self.frames):06d}.png"
                path.write_bytes(data)
                self.frames.append((stamp, path))
        except Exception:
            self.error = ShowrunError("capture_invalid", "Capture failed or exhausted its frame storage grant.")
        finally:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    async def finish(self):
        if not self.cdp:
            return None
        end = time.monotonic() + self.epoch_offset
        await self.cdp.send("Page.stopScreencast")
        self.accepting = False
        if self.tasks:
            await asyncio.gather(*self.tasks)
        if self.error:
            raise self.error
        require(bool(self.frames), "No captured frames.", "capture_invalid")
        # Compositor timestamps (Unix seconds) become media origin 0. Durations
        # are preserved, then quantized to 25 fps, not compressed or trimmed.
        lines = []
        for i, (stamp, path) in enumerate(self.frames):
            next_stamp = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
            lines.extend([f"file 'frames/{path.name}'", f"duration {max(.000001, next_stamp - stamp):.6f}"])
        lines.append(f"file 'frames/{self.frames[-1][1].name}'")
        concat = self.folder / "capture.ffconcat"
        concat.write_text("\n".join(lines) + "\n")
        output = self.folder / "capture.mp4"
        await command("ffmpeg", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(concat),
                      "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                      "-vf", "fps=25,setsar=1", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                      str(output), timeout=30)
        media = await inspect_media(output)
        require((media["width"], media["height"]) == (self.geometry["width"], self.geometry["height"]),
                "Captured geometry differs from the request; no fitting was authorized.", "capture_geometry")
        require(abs(media["duration_seconds"] - (end - self.origin)) <= .15,
                "Video timebase does not cover the continuous capture interval.", "capture_timing")
        media["timebase"] = {"unit": "seconds", "origin": "first compositor frame at media time zero",
                             "precision_seconds": .08, "frame_rate": 25,
                             "method": "Chrome screencast timestamps, monotonic clock mapped to Unix epoch",
                             "limitations": "Compositor capture is sampled, not proof of every display refresh."}
        # Only after a decoded handoff exists: discard redundant private PNGs.
        shutil.rmtree(self.frame_dir)
        concat.unlink()
        return media