# macOS desktop companion v0.6.0 - early access

This Apple Silicon macOS 14+ update adds **opt-in audio capture** for native
macOS takes. It uses ScreenCaptureKit and needs no virtual audio device. Windows
remains pinned to `desktop-v0.2.0`. Historical release assets are unchanged.

## Install

```sh
uv tool install --force 'git+https://github.com/robotdad/amplifier-smart-tool-showrun@main'
showrun prepare-desktop
showrun desktop-status
# only if takes will record a microphone (macOS 15+):
showrun desktop-status --request-microphone
```

The installer verifies the pinned SHA-256 and the app signature. The companion is
ad-hoc signed and not notarized. Updating it invalidates earlier Accessibility and
Screen & System Audio Recording grants: remove and re-add Showrun Desktop in both
lists, then recheck `desktop-status`. Output audio falls under the Screen & System
Audio Recording grant. Microphone capture additionally needs Microphone
permission, which only the explicit `--request-microphone` setup prompts for.

## Changes

- `capture.audio` for `macos` targets: `output` (`application` = the bound app plus
  its `<bundle>.` helper processes, refreshed mid-take; or `system` = everything
  except Showrun), optional `include_bundle_ids`, `microphone`,
  `microphone_device` and `require_signal` (default true). Audio is absent
  unless requested, so historical request fingerprints are unchanged.
- The companion writes host-clock-placed PCM per source. Showrun aligns it to the
  first window sample, muxes one AAC 48 kHz stereo track, and re-decodes the result.
- The receipt `audio` block reports sources, bundles actually included, device,
  sample rate/channels, buffer/gap statistics, companion and independent FFmpeg
  levels, a `signal` verdict per source and for the final track, sync offset and
  precision, and permission states.
- Explicit failures: `audio_unsupported`, `audio_unavailable`,
  `audio_permission_missing`, `audio_device_not_found`, `audio_start_failed`,
  `audio_interrupted`, `audio_no_signal` and `audio_failed`. Footage stays retained
  and labeled; a silent or mute take is never reported as a successful audio take.
- `desktop-status` reports `microphone` and `audio.{output_ready, microphone_ready}`;
  the companion advertises `audio_output`, `audio_microphone` and `request_microphone`.
- Web, Stories and Windows recordings remain silent and reject `capture.audio`.

## Evidence and limits

This exact signed candidate was verified on macOS 26.7 arm64 with the bundled Tone Fixture
(`fixtures/audio/`), using real ScreenCaptureKit capture through `showrun record`. The fixture
alternates a 440 Hz tone at -12 dBFS with silence, and its window turns white or black in step.
No model calls were made; the fixture's steps are satisfied by observation.

- **Application output** (`output: application`): the take succeeded with a 1280 x 720 H.264 video
  and a 48 kHz stereo AAC track, both 12.24 s. The companion measured a
  -11.6 dBFS peak across 614 buffers with host-clock timestamps. FFmpeg
  measured the delivered track independently at -11.5 dB max and
  -18.0 dB mean, so `signal` is `present`. `inspect` re-decoded
  it and matched the hash.
- **Sync:** `fixtures/audio/analyze.py` matched all 8 picture changes to sound changes.
  Sound led picture by a median of 0.1 s, with a worst case of
  0.246 s. This is consistent with window frames sampled at
  5 Hz and stamped on completion, and the receipt states 0.3 s precision.
- **Microphone** (`microphone_device: "BlackHole 2ch"`): a 660 Hz tone was fed into BlackHole while
  the fixture played. The microphone source measured -24.1 dBFS, matching the injected
  level. Band-pass checks of the single delivered track found both tones: 440 Hz at -11.8 dB and
  660 Hz at -20.7 dB, against -37.4 dB at a 1500 Hz control.
- **Failing before capture:** an earlier attempt requested a size the window could not reach. It
  failed with `capture_geometry` before audio started (`audio.status: not_started`); the fixture now
  centers its window.

Output-audio attribution was verified for a single-process app only. Browsers play web audio from
helper processes: `application` scope includes `<bundle>.`-prefixed helpers and refreshes them
mid-take, but Edge/Chromium capture was not part of this evidence. This is not a model-driven demo
or a watchability acceptance.

Qualified binary SHA-256: `55f98d98fb1d6e4a902897c05bdf462c6a5d9838134dec0f927c7a0ffbd8d1c3`.
Qualified Swift source SHA-256: `60a7a9f7620fe79e52a69f60aa6f8d8fb2a204998b6a853710ee1c175e876e80`.

Asset: `showrun-desktop-macos-arm64.zip`

SHA-256: `30615d548b22ea610faca590933f5f2d9d84fa771078f9204a1bd639b2b61c26`
