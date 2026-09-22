"""Argument/file I/O only; every capability and help body belongs to the library."""

import argparse
import json
import sys
from pathlib import Path

from .errors import ShowrunError
from .lib import CAPABILITIES, Showrun


def parser():
    result = argparse.ArgumentParser(prog="showrun", add_help=False)
    result.add_argument("-h", action="help", help="Terse command summary.")
    result.add_argument("--storage", help="Retained request/artifact store.")
    result.add_argument("--provider", choices=["openai", "anthropic"])
    result.add_argument("--model", help="Concrete model ID; never automatically selected.")
    result.add_argument("--credential-env", help="Explicit credential environment variable name.")
    sub = result.add_subparsers(dest="capability", required=True)
    for name, (_, description) in CAPABILITIES.items():
        command = sub.add_parser(name, help=description, description=description, add_help=False)
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
                         "begin-intent", "ack-intent", "reject-intent", "mp4", "zip", "serve"],
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
    if op == "serve":
        service = api.review_server(
            args.host, args.port, args.token, workspace_id=args.workspace,
            demo_ids=args.demo,
        )
        result = service.info()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        try:
            service._thread.join()
        except KeyboardInterrupt:
            service.stop()
        return result
    raise ShowrunError("input_error", "Unsupported review operation.")


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
    api = Showrun(args.storage, model)
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