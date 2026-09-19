"""Small explicit request boundary; no extensible executable inputs."""

import copy
import re
from pathlib import Path
from urllib.parse import urlsplit

from .errors import require


def obj(value, allowed, required=()):
    require(isinstance(value, dict), "Expected an object.")
    require(set(value) <= set(allowed) and set(required) <= set(value), "Unknown or missing fields.")


def text(value, maximum=4000):
    require(isinstance(value, str) and 0 < len(value) <= maximum, "Expected nonempty bounded text.")


def ident(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value), "Invalid identity.")


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "Integer outside supported bounds.")


def validate_model(model):
    """Validate optional inference controls without importing a provider."""
    obj(model, {"provider", "model", "credential_env", "response_tokens", "reasoning_effort"},
        {"provider", "model"})
    require(model["provider"] in {"openai", "anthropic"}, "MVP providers: openai or anthropic.")
    text(model["model"], 150)
    require(not model["model"].endswith("-latest"), "Select a concrete model ID, not a latest alias.")
    integer(model.get("response_tokens", 2048), 512, 4096)
    if "reasoning_effort" in model:
        require(model["provider"] == "openai", "reasoning_effort is supported only for OpenAI in this MVP.")
        require(isinstance(model["reasoning_effort"], str)
                and model["reasoning_effort"] in {"low", "medium", "high"},
                "reasoning_effort must be low, medium or high; unsupported settings never fall back.")
    if "credential_env" in model:
        require(isinstance(model["credential_env"], str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", model["credential_env"]),
                "Invalid credential env name.")


def origin(url):
    require(isinstance(url, str) and not any(c.isspace() or ord(c) < 32 for c in url),
            "URLs must not contain whitespace or control characters.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        require(False, "Invalid target URL or port.")
    require(port is None or 0 < port <= 65535, "Invalid target port.")
    require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password, "Only HTTP(S) targets without embedded credentials are supported.")
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_new(request):
    """Additional admission rule, applied only after atomic retained-key comparison."""
    if request["target"]["kind"] == "stories":
        require("fixture_sha256" in request["target"],
                "New managed takes require prepare-fixture; legacy takes remain inspectable.", "fixture_invalid")


def validate(request, model):
    obj(request, {"request_id", "target", "starting_state", "steps", "context", "capture", "authority"},
        {"request_id", "target", "starting_state", "steps", "authority"})
    value = copy.deepcopy(request)
    ident(value["request_id"])
    text(value["starting_state"])
    text(value.setdefault("context", "Prepared demonstration; no production claims."))
    target = value["target"]
    require(isinstance(target, dict), "Target must be an object.")
    if target.get("kind") == "url":
        obj(target, {"kind", "url", "origins", "stories_revision"},
            {"kind", "url", "origins"})
        text(target["url"])
        require(isinstance(target["origins"], list) and len(target["origins"]) == 1,
                "The MVP supports exactly one interaction/resource origin.")
        require(target["origins"] == [origin(target["url"])], "Declare the exact entry origin.")
        parsed = urlsplit(target["url"])
        require(not parsed.query, "Query-bearing entry URLs are unsupported; use a prepared dashboard.")
        if "stories_revision" in target:
            ident(target["stories_revision"])
    elif target.get("kind") == "stories":
        obj(target, {"kind", "python", "storage", "story_id", "revision_id", "fixture_sha256"},
            {"kind", "python", "storage", "story_id", "revision_id"})
        for key in ("python", "storage"):
            text(target[key])
            require(Path(target[key]).is_absolute(), "Stories interpreter and isolated store must be absolute.")
        require(Path(target["storage"]).resolve() != Path("~/.local/share/stories").expanduser().resolve(),
                "Use an isolated Stories fixture store, not the live store.")
        ident(target["story_id"])
        ident(target["revision_id"])
        # No new default: original fingerprints must still compare byte-for-byte.
        # Legacy requests may return retained results, but cannot launch new work.
        if "fixture_sha256" in target:
            require(isinstance(target["fixture_sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", target["fixture_sha256"]),
                    "Invalid fixture content identity.")
    else:
        require(False, "Supported targets are url and managed stories v0.1.0.")
    capture = value.setdefault("capture", {})
    obj(capture, {"width", "height"})
    for key, default in (("width", 1920), ("height", 1080)):
        integer(capture.setdefault(key, default), 320, 3840)
        require(capture[key] % 2 == 0, "Capture dimensions must be even; no implicit fitting.")
    authority = value["authority"]
    obj(authority, {"navigation_only", "disclose_dom", "max_seconds", "max_model_calls", "max_actions",
                    "stories_comment"},
        {"navigation_only", "disclose_dom", "max_seconds", "max_model_calls", "max_actions"})
    require(authority["disclose_dom"] is True, "Explicit DOM disclosure authority is required.")
    grant = authority.get("stories_comment")
    require("stories_comment" not in authority or grant is not None, "Comment grant must be an object, not null.")
    if grant is None:
        require(authority["navigation_only"] is True, "Navigation-only is required without a comment grant.")
    else:
        require(authority["navigation_only"] is False and target["kind"] == "stories"
                and "fixture_sha256" in target, "Comments require an explicit prepared managed Stories target.")
        obj(grant, {"story_id", "revision_id", "text"}, {"story_id", "revision_id", "text"})
        require(grant["story_id"] == target["story_id"] and grant["revision_id"] == target["revision_id"],
                "Comment authority must name the exact target story and revision.")
        text(grant["text"], 4000)
        require(bool(grant["text"].strip()), "Comment must contain text.")
    for key, cap in (("max_seconds", 180), ("max_model_calls", 12), ("max_actions", 30)):
        integer(authority[key], 1, cap)
    steps = value["steps"]
    require(isinstance(steps, list) and 0 < len(steps) <= 30, "Supply 1–30 ordered steps.")
    ids = set()
    for step in steps:
        obj(step, {"id", "instruction", "visible_text", "hold_seconds", "assertions"}, {"id", "instruction"})
        ident(step["id"])
        require(step["id"] not in ids, "Step IDs must be unique.")
        ids.add(step["id"])
        text(step["instruction"])
        require("visible_text" in step or bool(step.get("assertions")), "Supply an observable step check.")
        if "visible_text" in step:
            text(step["visible_text"], 1000)
        if "assertions" in step:
            require(isinstance(step["assertions"], list) and 0 < len(step["assertions"]) <= 8,
                    "Supply 1–8 declarative assertions.")
            for assertion in step["assertions"]:
                require(isinstance(assertion, dict), "Assertion must be an object.")
                kind = assertion.get("kind")
                if kind == "control":
                    obj(assertion, {"kind", "label", "visible"}, {"kind", "label", "visible"})
                    text(assertion["label"], 100)
                    require(type(assertion["visible"]) is bool, "Control visibility must be boolean.")
                elif kind == "review_panel":
                    obj(assertion, {"kind", "visible"}, {"kind", "visible"})
                    require(target["kind"] == "stories" and type(assertion["visible"]) is bool,
                            "Review panel check requires managed Stories and boolean visibility.")
                elif kind == "retained_comment":
                    obj(assertion, {"kind"}, {"kind"})
                    require(grant is not None, "Retained comment check requires exact comment authority.")
                else:
                    require(False, "Unsupported assertion kind.")
        integer(step.setdefault("hold_seconds", 3), 3, 60)
    require(sum(s["hold_seconds"] for s in steps) < authority["max_seconds"],
            "The elapsed grant must exceed required holds.")
    if grant is not None:
        require(any(a["kind"] == "retained_comment" for s in steps for a in s.get("assertions", [])),
                "Comment takes must check the retained comment, not just visible draft text.")
    if model is not None:
        validate_model(model)
    return value