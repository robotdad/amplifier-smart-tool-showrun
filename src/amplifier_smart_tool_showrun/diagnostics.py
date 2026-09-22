"""Read-only prerequisite probes shared by doctor, recording and inspection."""

import asyncio
import shutil
import sys
from pathlib import Path

from .errors import ShowrunError

MODES = ("web", "macos", "windows", "inspect")


def media_remedy():
    if sys.platform == "darwin":
        install = "On macOS with Homebrew, run `brew install ffmpeg`."
    elif sys.platform == "win32":
        install = "On Windows with winget, run `winget install --id Gyan.FFmpeg --exact`."
    else:
        install = ("On Debian/Ubuntu, run `sudo apt-get update && sudo apt-get install ffmpeg`. "
                   "On other Linux distributions, use your distribution's FFmpeg package.")
    return (
        f"{install} Alternatively obtain a build from https://ffmpeg.org/download.html. "
        "The build must include ffmpeg and ffprobe; recording also needs libx264. "
        "Add the directory containing both executables to PATH for the process running Showrun. "
        "After changing PATH, restart the terminal and any calling agent/service. "
        "Verify there with `ffmpeg -version`, `ffprobe -version`, and `showrun doctor` "
        "(or `showrun doctor --mode inspect` for inspection)."
    )


def media_error(name, detail):
    return ShowrunError("capture_dependency_missing", f"{name}: {detail}", media_remedy())


def _failed(name, error, **details):
    return {"name": name, "status": "failed", **details, "error": error.public()}


async def _probe(*args):
    """Only bounded version/encoder queries; no shell or media/target access."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 5)
        if proc.returncode:
            raise OSError("Prerequisite probe failed")
        return output.decode("utf-8", errors="replace")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def media_checks(recording=True):
    checks = []
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        version = None
        try:
            if not path:
                raise media_error(name, "executable not found on PATH.")
            output = await _probe(path, "-version")
            version = output.splitlines()[0] if output else ""
            if not version.startswith(f"{name} version "):
                raise media_error(name, "executable did not report a recognized version.")
            checks.append({"name": name, "status": "ready", "path": path, "version": version})
        except (OSError, TimeoutError):
            checks.append(_failed(name, media_error(name, "executable could not run successfully within 5 seconds."),
                                  path=path, version=version))
        except ShowrunError as exc:
            checks.append(_failed(name, exc, path=path, version=version))
    if recording:
        ffmpeg = checks[0]
        if ffmpeg["status"] != "ready":
            checks.append({"name": "libx264", "status": "not_checked", "reason": "ffmpeg is not runnable."})
        else:
            try:
                output = await _probe(ffmpeg["path"], "-hide_banner", "-encoders")
                if not any(len(fields := line.split()) >= 2 and fields[1] == "libx264"
                           for line in output.splitlines()):
                    raise media_error("libx264", "required recording encoder is not available in this FFmpeg build.")
                checks.append({"name": "libx264", "status": "ready", "path": ffmpeg["path"]})
            except (OSError, TimeoutError):
                checks.append(_failed("libx264", media_error("ffmpeg", "encoder query failed or timed out.")))
            except ShowrunError as exc:
                checks.append(_failed("libx264", exc))
    return checks


async def backend_checks(mode):
    if mode == "inspect":
        return []
    if mode in {"macos", "windows"}:
        expected = "darwin" if mode == "macos" else "win32"
        if sys.platform != expected:
            return [_failed("platform", ShowrunError(
                "desktop_unsupported", f"{mode} native recording requires {mode}.",
                "Run on the selected native platform; use --mode web for browser recording.",
            ))]
        if mode == "macos":
            from .desktop import helper_path
        else:
            from .windows_desktop import helper_path
        path = helper_path()
        if not path.is_file():
            return [_failed("companion", ShowrunError(
                "desktop_not_prepared", "Native companion is not prepared.",
                "Run `showrun prepare-desktop`, then `showrun desktop-status` to check permissions/session access.",
            ), path=str(path))]
        return [{"name": "companion", "status": "ready", "path": str(path),
                 "verification": "File presence only; companion was not launched."}]
    remedy = (
        f"Use this Showrun installation's Python ({sys.executable}) to run "
        "`python -m playwright install chromium`. If Playwright itself is missing or broken, "
        "reinstall Showrun in that environment first. Then rerun `showrun doctor --mode web`."
    )
    try:
        from playwright.async_api import async_playwright

        async def chromium_path():
            async with async_playwright() as pw:
                return pw.chromium.executable_path

        path = await asyncio.wait_for(chromium_path(), 10)
        if not Path(path).is_file():
            return [_failed("chromium", ShowrunError(
                "capture_dependency_missing", "Playwright Chromium executable is missing.", remedy,
            ), path=path)]
        return [{"name": "chromium", "status": "ready", "path": path,
                 "verification": "Playwright driver and executable presence; browser was not launched."}]
    except Exception:
        return [_failed("chromium", ShowrunError(
            "capture_dependency_missing", "Playwright/Chromium readiness could not be checked.", remedy,
        ))]


def retry_error(error, operation):
    if operation == "record":
        retry = (" Inspect the retained failed take. After correction and checking the starting state, "
                 "use a new request_id for a deliberate recording; an exact retry returns the retained failure.")
    elif operation == "inspect":
        retry = " After correction, retry inspection of the same take; no new request_id is needed."
    else:
        retry = ""
    return ShowrunError(error.code, error.message, error.remedy + retry)


def require_checks(checks, operation=None):
    for check in checks:
        if check["status"] == "failed":
            error = check["error"]
            raise retry_error(ShowrunError(error["code"], error["message"], error["remedy"]), operation)


async def preflight(mode):
    require_checks(await media_checks(), "record")
    require_checks(await backend_checks(mode), "record")


async def doctor(mode="web"):
    if mode not in MODES:
        raise ShowrunError("invalid_request", "Unknown doctor mode.", "Choose web, macos, windows or inspect.")
    checks = await media_checks(recording=mode != "inspect")
    checks.extend(await backend_checks(mode))
    limitations = ["Checks prerequisites only, not successful capture or media validity."]
    if mode != "inspect":
        limitations.append("Agent runtime, provider credentials, target readiness and actual encoding are not checked.")
        if mode == "web":
            limitations.append("No browser is launched; browser OS libraries and launch readiness are not checked.")
        else:
            limitations.append("Companion launch, OS version/architecture, permissions and interactive session are not checked. "
                               "Run showrun desktop-status separately; it launches the companion.")
    ready = all(check["status"] == "ready" for check in checks)
    return {"status": "ready" if ready else "failed", "ready": ready, "mode": mode,
            "checks": checks, "limitations": limitations, "model_calls": 0}