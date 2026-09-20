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
        precision = interval["precision_seconds"]
        check(precision in {.08, .5} and math.isfinite(duration))
        check(started <= start <= hs <= he == ended == end <= duration + precision)
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
        self.latest_stamp = None
        self.sampled_buckets = {}
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
                if self.origin is None:
                    self.origin = stamp
                # Chromium may deliver concurrently encoded compositor frames a
                # little out of order on animated pages. Retain their original
                # timestamps and sort at finalization; never retime the scene.
                require(math.isfinite(stamp) and stamp >= self.origin
                        and (self.latest_stamp is None or stamp >= self.latest_stamp - .25),
                        "Compositor clock moved outside the 250ms reorder window.", "capture_invalid")
                self.latest_stamp = max(self.latest_stamp or stamp, stamp)
                # Retain the first AND latest frame in each 50ms bucket. Keeping
                # only the first can lose a final UI update forever on a static page.
                bucket = int((stamp - self.origin) * 20 + 1e-6)
                slots = self.sampled_buckets.setdefault(bucket, [])
                index = None
                if len(slots) == 2:
                    first, last = sorted(slots, key=lambda i: self.frames[i][0])
                    if stamp < self.frames[first][0]:
                        index = first
                    elif stamp > self.frames[last][0]:
                        index = last
                    else:
                        return
                data = base64.b64decode(event["data"])
                previous_bytes = self.frames[index][1].stat().st_size if index is not None else 0
                self.bytes += len(data) - previous_bytes
                require(self.bytes <= 512 * 1024 * 1024, "Capture reached its 512 MiB frame limit.", "storage_limit")
                if index is None:
                    index = len(self.frames)
                    path = self.frame_dir / f"{index:06d}.png"
                    slots.append(index)
                    self.frames.append((stamp, path))
                else:
                    path = self.frames[index][1]
                    self.frames[index] = (stamp, path)
                path.write_bytes(data)
        except ShowrunError as exc:
            self.error = exc
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
        return await self.finalize_frames(end)

    async def finalize_frames(self, end):
        """Encode timestamped PNG samples without removing waits or failed actions."""
        if self.error:
            raise self.error
        require(bool(self.frames), "No captured frames.", "capture_invalid")
        self.frames.sort(key=lambda frame: frame[0])
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
                      # The final duplicate supplies the last PNG's hold boundary.
                      # Some FFmpeg versions extend it by the preceding duration;
                      # cap only that encoder padding at the observed capture end.
                      "-t", f"{end - self.origin:.6f}",
                      str(output), timeout=30)
        media = await inspect_media(output)
        require((media["width"], media["height"]) == (self.geometry["width"], self.geometry["height"]),
                "Captured geometry differs from the request; no fitting was authorized.", "capture_geometry")
        expected = end - self.origin
        media["timing"] = {"captured_seconds": expected,
                           "duration_delta_seconds": media["duration_seconds"] - expected,
                           "verified": abs(media["duration_seconds"] - expected) <= .15}
        if not media["timing"]["verified"]:
            media["timing"]["warning"] = (
                "Decoded footage retained; duration differs from capture clock. Step timing is approximate.")
        media["timebase"] = {"unit": "seconds", "origin": "first compositor frame at media time zero",
                             "precision_seconds": .08, "frame_rate": 25,
                             "method": "Chrome screencast timestamps, monotonic clock mapped to Unix epoch",
                             "limitations": "First and latest compositor frames per 50ms bucket are sampled into 25fps video; not every display refresh."}
        # Only after a decoded handoff exists: discard redundant private PNGs.
        shutil.rmtree(self.frame_dir)
        concat.unlink()
        return media
