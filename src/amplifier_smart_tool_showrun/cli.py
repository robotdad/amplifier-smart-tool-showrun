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
        elif name == "prepare-fixture":
            command.add_argument("presentation", help="Supplied exported presentation JSON file.")
            command.add_argument("--destination", required=True, help="Fresh empty fixture directory.")
            command.add_argument("--python", required=True, help="Installed Stories interpreter.")
    return result


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
        elif args.capability == "prepare-fixture":
            result = method(json.loads(Path(args.presentation).read_text()), args.destination, args.python)
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