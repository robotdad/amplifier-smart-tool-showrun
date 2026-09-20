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
