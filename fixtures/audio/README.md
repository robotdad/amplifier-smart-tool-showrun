# Audio verification fixture (macOS)

`ShowrunToneFixture.app` is a window titled **Showrun Tone Fixture** (bundle
`org.showrun.ToneFixture`). It alternates a 440 Hz sine at -12 dBFS with digital
silence. The window is white with `TONE ON` while sound plays, and black with
`TONE OFF` while silent. Picture and sound switch together, so one recording can
check both that the audio track has signal and that the sound lines up with the
picture. It needs no permissions and contains no data.

```sh
python fixtures/audio/build_macos.py /tmp/fixture      # Apple Command Line Tools
open -n /tmp/fixture/ShowrunToneFixture.app --args --period 1.5 --seconds 60
```

The tone plays through the current output device at the current volume. Record it
as a `macos` target with `capture.audio: {"output": "application"}`, then check the
`receipt.audio` signal fields and, independently, compare
`ffmpeg -af silencedetect` boundaries with the white/black frame changes.
It is a test instrument, not evidence that any other app's audio is attributed to
its bundle (browsers play audio from helper processes; see SMART_TOOL.md).
