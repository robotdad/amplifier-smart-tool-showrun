"""Animated Chromium pages can deliver encoded frames out of timestamp order."""
import asyncio
import base64

from amplifier_smart_tool_showrun.capture import Capture


class CDP:
    async def send(self, *args):
        pass


def test_bounded_reordering_preserves_compositor_time(tmp_path, monkeypatch):
    from amplifier_smart_tool_showrun import capture as module

    cap = Capture(tmp_path, {"width": 320, "height": 320})
    cap.frame_dir = tmp_path / "frames"
    cap.frame_dir.mkdir()
    cap.cdp = CDP()
    cap.epoch_offset = 0
    monkeypatch.setattr(module.time, "monotonic", lambda: 11)
    seen = []

    async def command(*args, **kwargs):
        seen.extend((tmp_path / "capture.ffconcat").read_text().splitlines())

    async def inspect(path):
        return {"width": 320, "height": 320, "duration_seconds": 1}

    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(module, "inspect_media", inspect)

    async def run():
        for stamp in (10, 10.1, 10.05, 10.2):
            await cap._save_frame({"metadata": {"timestamp": stamp}, "data": base64.b64encode(b"frame").decode(),
                                   "sessionId": 1})
        assert cap.error is None
        assert cap.origin == 10
        await cap.finish()

    asyncio.run(run())
    assert seen[:8] == ["file 'frames/000000.png'", "duration 0.050000",
                        "file 'frames/000002.png'", "duration 0.050000",
                        "file 'frames/000001.png'", "duration 0.100000",
                        "file 'frames/000003.png'", "duration 0.800000"]


def test_clock_regression_still_fails(tmp_path):
    cap = Capture(tmp_path, {"width": 320, "height": 320})
    cap.frame_dir = tmp_path
    cap.cdp = CDP()

    async def run():
        for stamp in (10, 11, 10.5):
            await cap._save_frame({"metadata": {"timestamp": stamp}, "data": "eA==", "sessionId": 1})

    asyncio.run(run())
    assert cap.error.code == "capture_invalid"
    assert "reorder window" in cap.error.message


def test_high_refresh_rate_has_bounded_frame_storage(tmp_path):
    cap = Capture(tmp_path, {"width": 320, "height": 320})
    cap.frame_dir = tmp_path
    cap.cdp = CDP()

    async def run():
        for index in range(120):
            await cap._save_frame({"metadata": {"timestamp": 10 + index / 120},
                                   "data": "eA==", "sessionId": 1})

    asyncio.run(run())
    assert cap.error is None
    assert len(cap.frames) == 40
    assert cap.bytes == 40
    assert len(list(tmp_path.glob("*.png"))) == 40


def test_final_static_update_is_not_dropped(tmp_path):
    cap = Capture(tmp_path, {"width": 320, "height": 320})
    cap.frame_dir = tmp_path
    cap.cdp = CDP()
    async def run():
        for stamp, data in ((10, b"old"), (10.01, b"pending"), (10.02, b"saved")):
            await cap._save_frame({"metadata": {"timestamp": stamp},
                                   "data": base64.b64encode(data).decode(), "sessionId": 1})
    asyncio.run(run())
    assert [(stamp, path.read_bytes()) for stamp, path in cap.frames] == [(10, b"old"), (10.02, b"saved")]


def _png(color, tmp_path):
    import subprocess

    path = tmp_path / f"{color}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90", "-frames:v", "1",
                    str(path)], check=True)
    return base64.b64encode(path.read_bytes()).decode()


def _color_at(path, seconds):
    import subprocess

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return max(range(3), key=lambda i: raw[i])  # dominant channel: 0 red, 1 green, 2 blue


def _feed(tmp_path, monkeypatch, seconds, **limits):
    """Continuous animation: 30 frames/s whose color changes every second."""
    from amplifier_smart_tool_showrun import capture as module

    for name, value in limits.items():
        monkeypatch.setattr(module, name, value)
    colors = {c: _png(c, tmp_path) for c in ("red", "lime", "blue")}
    folder = tmp_path / "take"
    folder.mkdir()
    cap = Capture(folder, {"width": 160, "height": 90})
    cap.frame_dir = folder / "frames"
    cap.frame_dir.mkdir()
    cap.cdp = CDP()

    async def run():
        for i in range(int(seconds * 30)):
            stamp = 10 + i / 30
            color = ("red", "lime", "blue")[int(i / 30) % 3]
            await cap._save_frame({"metadata": {"timestamp": stamp}, "data": colors[color], "sessionId": 1})
            await asyncio.sleep(0)
            if cap.flush_task and i % 30 == 0:
                await cap.flush_task  # let the background encoder keep pace in this fast synthetic feed
        cap.accepting = False
        return await cap.finalize_frames(10 + seconds)

    return cap, folder, asyncio.run(run())


def test_busy_capture_encodes_incrementally_without_drift(tmp_path, monkeypatch):
    cap, folder, media = _feed(tmp_path, monkeypatch, 7, SEGMENT_BYTES=6000)
    assert media["encoding"]["method"] == "incremental" and media["encoding"]["segments"] >= 3
    assert "capture_interrupted" not in media
    assert media["timing"]["verified"], media["timing"]
    assert abs(media["duration_seconds"] - 7) <= .08
    # Colors change on whole seconds; segment joins on the 0.2 s grid must not shift them.
    for second in range(7):
        assert _color_at(folder / "capture.mp4", second + .5) == (0, 1, 2)[second % 3]
        assert _color_at(folder / "capture.mp4", second + .08) == (0, 1, 2)[second % 3]
    assert not (folder / "frames").exists() and not (folder / "segments").exists()
    assert not list(folder.glob("*.ffconcat"))


def test_storage_limit_keeps_footage_recorded_up_to_the_limit(tmp_path, monkeypatch):
    from amplifier_smart_tool_showrun import capture as module

    size = len(base64.b64decode(_png("red", tmp_path)))
    # No incremental relief: the un-encoded bound trips after ~2 s of frames.
    cap, folder, media = _feed(tmp_path, monkeypatch, 4, SEGMENT_BYTES=10**12, FRAME_LIMIT_BYTES=size * 60)
    assert cap.error.code == "storage_limit"
    assert media["capture_interrupted"]["code"] == "storage_limit"
    assert (folder / "capture.mp4").exists() and not (folder / "frames").exists()
    assert 1.8 <= media["duration_seconds"] <= 2.2
    assert _color_at(folder / "capture.mp4", .5) == 0 and _color_at(folder / "capture.mp4", 1.5) == 1
    assert module.FRAME_LIMIT_BYTES == size * 60
