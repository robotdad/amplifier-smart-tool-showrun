"""Argument/file I/O only; every capability and help body belongs to the library."""

import argparse
import json
import os
import sys
from pathlib import Path

from .errors import ShowrunError
from .lib import CAPABILITIES, Showrun


def parser():
    result = argparse.ArgumentParser(
        prog="showrun", add_help=False,
        epilog="Start with: showrun doctor. Use showrun <command> -h for flags, "
               "showrun <command> --help for its operating guide, or showrun --help for the full guide.")
    result.add_argument("-h", action="help", help="Terse command summary.")
    result.add_argument("--storage", help="Retained request/artifact store.")
    result.add_argument("--provider", choices=["openai", "anthropic"])
    result.add_argument("--model", help="Concrete model ID; never automatically selected.")
    result.add_argument("--credential-env", help="Explicit credential environment variable name.")
    result.add_argument("--auth-root", help="Saved sign-in store (default: $XDG_STATE_HOME/showrun-auth).")
    sub = result.add_subparsers(dest="capability", required=True)
    for name, (_, description) in CAPABILITIES.items():
        command = sub.add_parser(
            name, help=description, description=description, add_help=False,
            epilog=f"Operating guide: showrun {name} --help. Full guide: showrun --help. "
                   "Global options precede the command.")
        command.add_argument("-h", action="help")
        if name in {"record", "validate"}:
            command.add_argument("request", help="JSON file containing request data.")
        elif name in {"status", "inspect", "cancel"}:
            command.add_argument("request_id")
        elif name == "doctor":
            from .diagnostics import MODES

            command.add_argument("--mode", choices=MODES, default="web", help="Prerequisites to check (default: web).")
        elif name == "prepare-desktop":
            command.add_argument("--build", action="store_true", help="Compile locally instead of downloading the pinned release.")
        elif name == "desktop-status":
            command.add_argument("--request-microphone", action="store_true",
                                 help="Explicitly show the macOS Microphone prompt once (needed only for microphone audio).")
        elif name == "auth":
            command.add_argument("operation", choices=["prepare", "list", "delete"])
            command.add_argument("name", nargs="?", help="Saved sign-in profile name.")
            command.add_argument("--url", help="Entry URL whose origin the sign-in is for.")
            command.add_argument("--ready-text", help="Finish automatically when this text shows on the signed-in page.")
            command.add_argument("--timeout", type=int, default=300, help="Seconds to wait for sign-in (default 300).")
            command.add_argument("--replace", action="store_true", help="Replace an existing saved sign-in.")
        elif name == "prepare-fixture":
            command.add_argument("presentation", help="Supplied exported presentation JSON file.")
            command.add_argument("--destination", required=True, help="Fresh empty fixture directory.")
            command.add_argument("--python", required=True, help="Installed Stories interpreter.")
        elif name == "review":
            command.add_argument(
                "operation",
                nargs="?",
                default="state",
                choices=["state", "list", "demo", "take", "clip", "select", "rename",
                         "prepare-delete", "delete", "draft", "note", "notes", "playback", "appearance",
                         "begin-intent", "ack-intent", "reject-intent", "mp4", "zip", "serve", "bootstrap"],
            )
            command.add_argument("identifiers", nargs="*")
            command.add_argument("--workspace", default="default")
            command.add_argument("--demo", action="append",
                                 help="Optional explicit demo scope; repeat to authorize more demos.")
            command.add_argument("--scope", choices=["clip", "take", "demo"])
            command.add_argument("--name")
            command.add_argument("--text")
            command.add_argument("--step-id")
            command.add_argument("--time-seconds", type=float)
            command.add_argument("--range-start-seconds", type=float)
            command.add_argument("--range-end-seconds", type=float)
            command.add_argument("--expected-version", type=int)
            command.add_argument("--request-id")
            command.add_argument("--confirmation-token")
            command.add_argument("--output")
            command.add_argument("--payload", help="JSON file for an exact review intent envelope.")
            command.add_argument("--kind", choices=["save_draft", "submit_note"])
            command.add_argument("--error-code")
            command.add_argument("--message")
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=0)
            command.add_argument("--token")
            command.add_argument("--control-file",
                                 help="serve: create this private (0600) file holding the server's API token; "
                                      "bootstrap: read it to mint a fresh one-time browser URL.")
    return result


def _review_command(api, args):
    scopes = {args.workspace: None if args.demo is None else set(args.demo)}
    store = api.review_store(workspace_scopes=scopes)
    op = args.operation
    ids = args.identifiers
    if op in {"demo", "take", "clip", "playback", "appearance", "begin-intent", "ack-intent", "reject-intent"} and len(ids) != 1:
        raise ShowrunError("input_error", f"review {op} needs exactly one identifier or value.")
    if op == "notes":
        return {"workspace_id": args.workspace, "notes": store.notes(args.workspace, ids[0] if ids else None)}
    if op == "playback":
        return store.set_playback(args.workspace, ids[0], args.time_seconds)
    if op == "appearance":
        return store.set_appearance(args.workspace, ids[0], args.expected_version, args.request_id)
    if op == "begin-intent":
        if not args.payload or not args.kind:
            raise ShowrunError("input_error", "begin-intent needs --kind and a --payload JSON file.")
        return store.begin_intent(args.workspace, ids[0], args.kind, json.loads(Path(args.payload).read_text()))
    if op == "ack-intent":
        return store.ack_intent(args.workspace, ids[0])
    if op == "reject-intent":
        if not args.error_code or not args.message:
            raise ShowrunError("input_error", "reject-intent needs --error-code and --message.")
        return store.reject_intent(args.workspace, ids[0], args.error_code, args.message)
    if op == "state":
        return store.workspace(args.workspace)
    if op == "list":
        return store.list_demos(args.workspace)
    if op == "demo":
        return store.get_demo(ids[0], workspace_id=args.workspace)
    if op == "take":
        return store.get_take(ids[0], workspace_id=args.workspace)
    if op == "clip":
        return store.get_clip(ids[0], workspace_id=args.workspace)
    if op == "select":
        if len(ids) != 3 or args.expected_version is None or not args.request_id:
            raise ShowrunError("input_error", "review select needs DEMO_ID TAKE_ID CLIP_ID, --expected-version and --request-id.")
        return store.select_clip(args.workspace, ids[0], ids[1], ids[2], args.expected_version, args.request_id,
                                 step_id=args.step_id, time_seconds=args.time_seconds)
    if op == "rename":
        if len(ids) != 1 or args.scope not in {"demo", "take", "clip"} or not args.name \
                or args.expected_version is None or not args.request_id:
            raise ShowrunError("input_error", "review rename needs SCOPE ID --name --expected-version --request-id.")
        return store.rename(args.scope, ids[0], args.name, args.expected_version, args.request_id,
                            workspace_id=args.workspace)
    if op == "prepare-delete":
        if len(ids) != 1 or args.scope not in {"demo", "take", "clip"} or args.expected_version is None:
            raise ShowrunError("input_error", "review prepare-delete needs --scope ID --expected-version.")
        return store.prepare_delete(args.scope, ids[0], args.expected_version, args.request_id,
                                    workspace_id=args.workspace)
    if op == "delete":
        token = args.confirmation_token or (ids[0] if ids else None)
        if not token:
            raise ShowrunError("input_error", "review delete needs --confirmation-token or TOKEN.")
        return store.delete(token, args.request_id, workspace_id=args.workspace)
    if op in {"draft", "note"}:
        if len(ids) != 1 or not args.text:
            raise ShowrunError("input_error", f"review {op} needs CLIP_ID and --text.")
        common = {
            "step_id": args.step_id, "time_seconds": args.time_seconds,
            "range_start_seconds": args.range_start_seconds, "range_end_seconds": args.range_end_seconds,
        }
        if op == "draft":
            return store.save_draft(args.workspace, ids[0], args.text, expected_version=args.expected_version,
                                    request_id=args.request_id, **{k: v for k, v in common.items() if v is not None})
        return store.submit_note(args.workspace, ids[0], text=args.text, expected_version=args.expected_version,
                                 request_id=args.request_id, **{k: v for k, v in common.items() if v is not None})
    if op == "mp4":
        if len(ids) != 1:
            raise ShowrunError("input_error", "review mp4 needs CLIP_ID.")
        data, info = store.download_mp4(args.workspace, ids[0])
        output = Path(args.output or info["filename"])
        with output.open("xb") as stream:
            stream.write(data)
        return {**info, "status": "downloaded", "output": str(output), "bytes": len(data)}
    if op == "zip":
        if len(ids) != 1:
            raise ShowrunError("input_error", "review zip needs DEMO_ID.")
        data, info = store.download_zip(args.workspace, ids[0])
        output = Path(args.output or f"{ids[0]}.zip")
        with output.open("xb") as stream:
            stream.write(data)
        return {**info, "status": "downloaded", "output": str(output), "bytes": len(data)}
    if op == "bootstrap":
        return _mint_bootstrap(args.control_file)
    if op == "serve":
        control = Path(args.control_file) if args.control_file else None
        if control:
            # Refuse before binding: an existing file may belong to another server.
            if control.exists():
                raise ShowrunError("input_error", "The review control file already exists.",
                                   "Choose a new --control-file path, or remove the file left by a stopped server.")
        service = api.review_server(
            args.host, args.port, args.token, workspace_id=args.workspace,
            demo_ids=args.demo,
        )
        result = service.info()
        if control:
            fd = os.open(control, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump({"schema_version": 1, "base_url": f"http://{service.host}:{service.port}",
                           "api_token": service.token, "pid": os.getpid()}, stream)
            result = {**result, "control_file": str(control)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        import signal

        def terminate(*_):
            raise KeyboardInterrupt

        # A terminated server stops like Ctrl-C, so its control file never outlives it.
        previous = signal.signal(signal.SIGTERM, terminate)
        try:
            service._thread.join()
        except KeyboardInterrupt:
            service.stop()
        finally:
            signal.signal(signal.SIGTERM, previous)
            if control:
                control.unlink(missing_ok=True)
        return result
    raise ShowrunError("input_error", "Unsupported review operation.")


def _mint_bootstrap(control_file):
    """Ask a running ``review serve`` for a fresh one-time browser URL, without a restart."""
    import urllib.error
    import urllib.request

    if not control_file:
        raise ShowrunError("input_error", "review bootstrap needs --control-file from review serve.",
                           "Start the server with review serve --control-file PATH, then pass the same PATH.")
    try:
        control = json.loads(Path(control_file).read_text())
        base, token = control["base_url"], control["api_token"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ShowrunError("input_error", "The review control file is missing or unreadable.",
                           "Check that review serve is still running with this --control-file.") from None
    request = urllib.request.Request(f"{base}/api/call", method="POST",
                                     data=json.dumps({"operation": "mint_bootstrap"}).encode(),
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        raise ShowrunError("review_unavailable", "The review server did not answer.",
                           "Check that review serve is still running; restart it if it stopped.") from None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv:
        scope = next((arg for arg in argv if arg in CAPABILITIES), None)
        print(Showrun.skill(scope))
        return 0
    args = parser().parse_args(argv)
    model = {"provider": args.provider, "model": args.model} if args.provider or args.model else None
    if model and args.credential_env:
        model["credential_env"] = args.credential_env
    api = Showrun(args.storage, model, auth_root=args.auth_root)
    try:
        method = getattr(api, args.capability.replace("-", "_"))
        if args.capability in {"record", "validate"}:
            result = method(json.loads(Path(args.request).read_text()))
        elif args.capability in {"status", "inspect", "cancel"}:
            result = method(args.request_id)
        elif args.capability == "doctor":
            result = method(mode=args.mode)
        elif args.capability == "prepare-desktop":
            result = method(build=args.build)
        elif args.capability == "desktop-status":
            result = method(request_microphone=args.request_microphone)
        elif args.capability == "auth":
            result = method(args.operation, args.name, args.url, args.ready_text, args.timeout, args.replace)
        elif args.capability == "prepare-fixture":
            result = method(json.loads(Path(args.presentation).read_text()), args.destination, args.python)
        elif args.capability == "review":
            result = _review_command(api, args)
        else:
            result = method()
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("status") in {"failed", "cancelled", "uncertain", "restricted"} else 0
    except ShowrunError as exc:
        print(json.dumps({"status": "failed", "error": exc.public()}))
        return 1
    except (ValueError, OSError, TypeError):
        print(json.dumps({"status": "failed", "error": {"code": "input_error",
                         "message": "Could not read valid inputs.", "remedy": "Check arguments and JSON file."}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())