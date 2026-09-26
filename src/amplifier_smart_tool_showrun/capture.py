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

from . import diagnostics
from .errors import ShowrunError, require


async def command(*args, timeout=30):
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        if args[0] in {"ffmpeg", "ffprobe"}:
            raise diagnostics.media_error(args[0], "executable could not be started; check installation and PATH.") from None
        raise
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        require(proc.returncode == 0, "Media processing failed.", "media_invalid")
        return output
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def preflight():
    await diagnostics.preflight("web")


async def inspect_media(path):
    # Also used when finalizing a recording; the public operation owns retry guidance.
    diagnostics.require_checks(await diagnostics.media_checks(recording=False))
    result = json.loads(await command("ffprobe", "-v", "error", "-show_streams", "-show_format",
                                      "-of", "json", str(path)))
    streams = [s for s in result["streams"] if s["codec_type"] == "video"]
    require(len(streams) == 1, "Expected one video stream.", "media_invalid")
    stream = streams[0]
    sounds = [s for s in result["streams"] if s["codec_type"] == "audio"]
    require(len(sounds) <= 1, "Expected at most one audio stream.", "media_invalid")
    await command("ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-", timeout=60)
    media = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "bytes": path.stat().st_size, "container": result["format"]["format_name"],
             "width": stream["width"], "height": stream["height"],
             "duration_seconds": float(result["format"]["duration"]),
             "display_aspect_ratio": stream.get("display_aspect_ratio"),
             "sample_aspect_ratio": stream.get("sample_aspect_ratio"),
             "audio": sounds[0].get("codec_name", "unknown") if sounds else "none", "decoded": True}
    if sounds:
        media["audio_stream"] = {"codec": sounds[0].get("codec_name"), "sample_rate": int(sounds[0].get("sample_rate", 0)),
                                 "channels": sounds[0].get("channels"),
                                 "duration_seconds": float(sounds[0].get("duration", 0) or 0)}
    return media


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


# Un-encoded PNG samples are bounded; long busy takes encode completed spans into
# H.264 segments while recording so continuous animation does not exhaust the bound.
FRAME_LIMIT_BYTES = 512 * 1024 * 1024
SEGMENT_BYTES = 96 * 1024 * 1024
ENCODED_LIMIT_BYTES = 4 * 1024 * 1024 * 1024
# Segment boundaries lie on a 0.2 s grid from media time zero: a whole number of
# both 50 ms sampling buckets and 25 fps output frames, so joins add no drift.
SEGMENT_TICK = .2
ENCODE_ARGS = ("-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
               "-vf", "fps=25,setsar=1", "-pix_fmt", "yuv420p")


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
        self.frame_names = 0
        self.segments = []          # encoded H.264 spans, in media order
        self.segment_ticks = 0      # current pending span starts at origin + ticks * SEGMENT_TICK
        self.carry = None           # frame shown at the pending span's start (last encoded frame)
        self.flush_task = None
        self.encoded_bytes = 0
        self.limit_stamp = None     # compositor time at which a storage bound stopped sampling

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
                if self.bytes > FRAME_LIMIT_BYTES:
                    self._stop_at_limit(stamp, "Capture reached its 512 MiB un-encoded frame limit.")
                    return
                if index is None:
                    index = len(self.frames)
                    # Names never repeat: incremental encoding shortens the frame list.
                    path = self.frame_dir / f"{self.frame_names:06d}.png"
                    self.frame_names += 1
                    slots.append(index)
                    self.frames.append((stamp, path))
                else:
                    path = self.frames[index][1]
                    self.frames[index] = (stamp, path)
                path.write_bytes(data)
                self._maybe_flush()
        except ShowrunError as exc:
            self.error = exc
        except Exception:
            self.error = ShowrunError("capture_invalid", "Capture failed or exhausted its frame storage grant.")
        finally:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def _bucket(self, stamp):
        return int((stamp - self.origin) * 20 + 1e-6)

    def _stop_at_limit(self, stamp, message):
        """Keep footage recorded up to a storage bound; it is delivered as interrupted."""
        self.accepting = False
        self.limit_stamp = stamp
        self.error = ShowrunError("storage_limit", message,
                                  "Footage up to the limit is retained as interrupted media; "
                                  "shorten the take or reduce continuous animation, then use a new request_id.")

    def _maybe_flush(self):
        """Hand completed spans to a background encoder (synchronous selection, no awaits)."""
        if self.flush_task or not self.accepting or self.bytes < SEGMENT_BYTES:
            return
        # Frames still accepted must be >= latest - 0.25 s; spans ending 0.5 s earlier are final.
        ticks = math.floor((self.latest_stamp - .5 - self.origin) / SEGMENT_TICK + 1e-9)
        if ticks <= self.segment_ticks:
            return
        limit = ticks * round(SEGMENT_TICK * 20)
        self.frames.sort(key=lambda frame: frame[0])
        done = [f for f in self.frames if self._bucket(f[0]) < limit]
        if not done:
            return
        keep = [f for f in self.frames if self._bucket(f[0]) >= limit]
        self.frames = keep
        self.sampled_buckets = {}
        for i, (stamp, _path) in enumerate(keep):
            self.sampled_buckets.setdefault(self._bucket(stamp), []).append(i)
        start = self.origin + self.segment_ticks * SEGMENT_TICK
        span = ([(start, self.carry)] if self.carry else []) + done
        previous_carry, self.carry = self.carry, done[-1][1]
        end = self.origin + ticks * SEGMENT_TICK
        self.segment_ticks = ticks
        release = [p for _s, p in done[:-1]] + ([previous_carry] if previous_carry else [])
        self.flush_task = asyncio.create_task(self._encode_segment(span, end, release))

    async def _encode_segment(self, span, end, release):
        try:
            directory = self.folder / "segments"
            directory.mkdir(mode=0o700, exist_ok=True)
            output = directory / f"{len(self.segments):04d}.mp4"
            await self._encode(span, end, output, f"segment-{output.stem}.ffconcat", timeout=180)
            self.segments.append(output)
            self.encoded_bytes += output.stat().st_size
            for path in release:
                self.bytes -= path.stat().st_size
                path.unlink()
            if self.encoded_bytes > ENCODED_LIMIT_BYTES:
                self._stop_at_limit(self.latest_stamp, "Capture reached its 4 GiB encoded footage limit.")
        except ShowrunError as exc:
            self.error = self.error or exc
            self.accepting = False
        except Exception:
            self.error = self.error or ShowrunError("capture_invalid", "Incremental encoding failed.")
            self.accepting = False
        finally:
            self.flush_task = None

    async def _encode(self, frames, end, output, concat_name, timeout=30):
        """Encode (stamp, png) samples held until the next stamp, the last until ``end``."""
        lines = []
        for i, (stamp, path) in enumerate(frames):
            next_stamp = frames[i + 1][0] if i + 1 < len(frames) else end
            lines.extend([f"file '{path.relative_to(self.folder).as_posix()}'",
                          f"duration {max(.000001, next_stamp - stamp):.6f}"])
        # The final duplicate supplies the last PNG's hold boundary.
        lines.append(f"file '{frames[-1][1].relative_to(self.folder).as_posix()}'")
        concat = self.folder / concat_name
        concat.write_text("\n".join(lines) + "\n")
        # Some FFmpeg versions extend the final duplicate by the preceding
        # duration; cap only that encoder padding at the span end.
        await command("ffmpeg", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(concat), *ENCODE_ARGS,
                      "-movflags", "+faststart", "-t", f"{end - frames[0][0]:.6f}", str(output), timeout=timeout)
        concat.unlink(missing_ok=True)

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
        while self.flush_task:
            await asyncio.gather(self.flush_task, return_exceptions=True)
        interrupted = None
        if self.error:
            if self.error.code != "storage_limit" or not (self.frames or self.segments):
                raise self.error
            # Preserve decodable footage recorded before the bound was reached.
            interrupted, end = self.error, min(end, self.limit_stamp)
        require(bool(self.frames or self.segments), "No captured frames.", "capture_invalid")
        self.frames.sort(key=lambda frame: frame[0])
        output = self.folder / "capture.mp4"
        if not self.segments:
            # Compositor timestamps (Unix seconds) become media origin 0. Durations
            # are preserved, then quantized to 25 fps, not compressed or trimmed.
            await self._encode(self.frames, end, output, "capture.ffconcat")
        else:
            start = self.origin + self.segment_ticks * SEGMENT_TICK
            tail = [(start, self.carry)] + self.frames  # every retained frame is after the last boundary
            last = self.folder / "segments" / f"{len(self.segments):04d}.mp4"
            if end - start > .02:
                await self._encode(tail, end, last, f"segment-{last.stem}.ffconcat", timeout=180)
                self.segments.append(last)
            joined = self.folder / "segments" / "join.ffconcat"
            joined.write_text("".join(f"file '{p.name}'\n" for p in self.segments))
            await command("ffmpeg", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(joined),
                          "-c", "copy", "-movflags", "+faststart", str(output), timeout=120)
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
        if self.segments:
            media["encoding"] = {"method": "incremental", "segments": len(self.segments),
                                 "boundary_grid_seconds": SEGMENT_TICK}
        if interrupted:
            media["capture_interrupted"] = interrupted.public()
        # Only after a decoded handoff exists: discard redundant private PNGs.
        shutil.rmtree(self.frame_dir)
        shutil.rmtree(self.folder / "segments", ignore_errors=True)
        return media
