"""Private child of a native fixture app. Mutations arrive only from its UI adapter."""

import argparse
import json
import os
import sys
from pathlib import Path

from model import Model


def persist(path, state, first=False):
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
    if first:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        return
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--platform", choices=["macos", "windows", "linux"], required=True)
    args = parser.parse_args()
    model = Model(args.run_id, args.platform)
    persist(args.state, model.snapshot(), first=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) - {"action", "value"}:
                raise ValueError("Invalid fixture command")
            before = model.state["revision"]
            state = model.apply(request["action"], request.get("value"))
            if state["revision"] != before:
                persist(args.state, state)
            print(json.dumps({"state": state}, ensure_ascii=False), flush=True)
        except Exception as error:
            print(json.dumps({"error": str(error)}), flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
