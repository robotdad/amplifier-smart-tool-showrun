"""Logging boundary regressions. SDK tests run in fresh, loopback-only processes."""
import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import QueueHandler
from pathlib import Path

import pytest

from amplifier_smart_tool_showrun import agent
from amplifier_smart_tool_showrun.errors import ShowrunError


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self):
        # Inspect ALL fields, not only what a default formatter happens to print.
        return "\n".join(repr(record.__dict__) for record in self.records)


@pytest.mark.parametrize("first_exit", ["normal", "exception", "cancel"])
def test_overlapping_scopes_lazy_loggers_and_independent_tasks(first_exit):
    original = logging.Logger.handle
    sink = Records()
    name = "new.transport." + first_exit
    assert name not in logging.Logger.manager.loggerDict

    async def run():
        entered, second_entered, first_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        independent_done = asyncio.Event()

        async def first():
            try:
                with agent.safe_provider_logs():
                    entered.set()
                    await second_entered.wait()
                    with agent.safe_provider_logs():  # Nested, not just overlapping.
                        logging.getLogger(name).error("PRIVATE nested", extra={"cookie": "PRIVATE"})
                    if first_exit == "exception":
                        raise ValueError("synthetic exit")
                    if first_exit == "cancel":
                        await asyncio.Event().wait()  # Cancelled by the independent task.
            except (ValueError, asyncio.CancelledError):
                pass
            finally:
                first_done.set()

        async def second():
            await entered.wait()
            with agent.safe_provider_logs():
                # Logger AND handler created after entering the scope. Direct
                # handlers, logger filters and non-propagation must all be safe.
                logger = logging.getLogger(name)
                logger.setLevel(logging.DEBUG)
                logger.addHandler(sink)
                logger.propagate = False
                filtered = []
                logger.addFilter(lambda record: filtered.append(record) or True)
                second_entered.set()
                await first_done.wait()
                await independent_done.wait()
                assert logging.Logger.handle is not original
                logger.debug("PRIVATE header", extra={"headers": ["PRIVATE"]})
                try:
                    raise ValueError("PRIVATE response")
                except ValueError:
                    logger.exception("PRIVATE %s", "body", extra={"raw": "PRIVATE"}, stack_info=True)
                # A to_thread helper inherits the dispatch's context.
                await asyncio.to_thread(logger.error, "PRIVATE propagated worker")
                assert all("PRIVATE" not in repr(r.__dict__) for r in filtered)
            logger.error("after scope")

        async def independent():
            await second_entered.wait()
            assert logging.Logger.handle is not original
            # Same transport logger during another task's provider dispatch.
            logging.getLogger(name).debug("independent debug", extra={"keep": "unaltered"})
            if first_exit == "cancel":
                first_task.cancel()
            independent_done.set()

        first_task = asyncio.create_task(first())
        await asyncio.gather(first_task, second(), independent())

    try:
        asyncio.run(run())
        assert "PRIVATE" not in sink.text()
        assert "independent debug" in sink.text() and "unaltered" in sink.text()
        assert "after scope" in sink.text()
        assert "sanitized Showrun error" in sink.text()
        assert logging.Logger.handle is original
        assert agent._provider_log_users == 0
    finally:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.filters.clear()
        logger.propagate = True
        sink.close()


def test_overlapping_thread_scopes_restore_only_after_last_exit():
    original = logging.Logger.handle
    sink = Records()
    logger = logging.getLogger("other.transport.threads")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(sink)
    logger.propagate = False
    entered = threading.Barrier(3)
    first_done = threading.Event()
    release = threading.Event()

    def first():
        with agent.safe_provider_logs():
            entered.wait(timeout=5)
            logger.error("PRIVATE first")
        first_done.set()

    def second():
        with agent.safe_provider_logs():
            entered.wait(timeout=5)
            assert first_done.wait(5)
            assert logging.Logger.handle is not original
            logger.error("PRIVATE second", extra={"raw": "PRIVATE"})
            assert release.wait(5)

    try:
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(first), pool.submit(second)
            entered.wait(timeout=5)
            assert first_done.wait(5)
            logger.debug("independent thread", extra={"keep": "unaltered"})
            release.set()
            a.result(timeout=5)
            b.result(timeout=5)
        assert "PRIVATE" not in sink.text()
        assert "independent thread" in sink.text() and "unaltered" in sink.text()
        assert logging.Logger.handle is original and agent._provider_log_users == 0
    finally:
        release.set()
        logger.removeHandler(sink)
        logger.propagate = True


def test_preserves_existing_logging_owner_and_does_not_overwrite_new_owner(monkeypatch):
    original = logging.Logger.handle
    seen = []

    def previous(logger, record):
        seen.append(record)
        return original(logger, record)

    monkeypatch.setattr(logging.Logger, "handle", previous)
    with agent.safe_provider_logs():
        logging.getLogger("owner.test").error("PRIVATE")
    assert logging.Logger.handle is previous
    assert "PRIVATE" not in repr(seen[0].__dict__)
    with agent.safe_provider_logs():
        # Do not revert someone else's explicit replacement on scope exit.
        monkeypatch.setattr(logging.Logger, "handle", original)
    assert logging.Logger.handle is original
    assert agent._provider_log_users == 0


def test_queue_handler_gets_only_sanitized_record_before_context_is_lost():
    original = logging.Logger.handle
    channel = queue.Queue()
    handler = QueueHandler(channel)
    logger = logging.getLogger("new.transport.queue")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        with agent.safe_provider_logs():
            logger.addHandler(handler)  # Handler created/attached after admission.
            logger.debug("PRIVATE debug")
            try:
                raise ValueError("PRIVATE exception")
            except ValueError:
                logger.exception("PRIVATE body", extra={"headers": "PRIVATE"}, stack_info=True)
        assert logging.Logger.handle is original
        with ThreadPoolExecutor(1) as pool:
            record = pool.submit(channel.get, True, 5).result(timeout=5)
        assert "PRIVATE" not in repr(record.__dict__)
        assert "sanitized Showrun error" in record.getMessage()
        assert channel.empty()
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
        handler.close()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_fresh_process_actual_mounted_sdk_loopback(provider):
    if not (agent._location()[1] / "showrun-ready.json").is_file():
        pytest.skip("Explicit showrun prepare-runtime has not been run in this installation.")
    env = dict(os.environ, SHOWRUN_LOG_TEST_KEY="SYNTHETIC_PRIVATE_KEY",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), provider],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "passed"


def loopback_trial(provider):
    """Actual mounted provider + lazy SDK + real HTTP, not MockTransport/complete."""
    sink = Records()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(sink)
    original = logging.Logger.handle
    release = threading.Event()
    received = None
    loop = None
    requests = []
    cookie, body = "SYNTHETIC_PRIVATE_COOKIE", "SYNTHETIC_PRIVATE_BODY"
    key, prompt = "SYNTHETIC_PRIVATE_KEY", "SYNTHETIC_PRIVATE_PROMPT"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append((self.command, self.path))
            loop.call_soon_threadsafe(received.set)
            assert release.wait(10)
            data = json.dumps({"type": "error", "error": {
                "type": "invalid_request_error", "message": body}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    def loopback_only(event, args):
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or address[:2] != ("127.0.0.1", port):
                raise AssertionError("External connection forbidden")
        if event == "socket.getaddrinfo" and args[0] not in ("127.0.0.1", b"127.0.0.1"):
            raise AssertionError("External DNS forbidden")

    sys.addaudithook(loopback_only)

    async def run():
        nonlocal received, loop
        loop = asyncio.get_running_loop()
        received = asyncio.Event()
        navigator = agent.Navigator({
            "provider": provider, "model": "claude-sonnet-5" if provider == "anthropic" else "gpt-6-astra",
            "credential_env": "SHOWRUN_LOG_TEST_KEY"})
        try:
            await navigator.start()
            url = f"http://127.0.0.1:{port}"
            if provider == "anthropic":
                assert navigator.provider._client is None
                # Test-only endpoint override; preserve actual lazy SDK construction.
                navigator.provider._base_url = url
                assert "httpcore2.http11" not in logging.Logger.manager.loggerDict
            else:
                navigator.provider.client.base_url = url + "/v1"
            assert navigator.provider._retry_config.max_retries == 0
            assert navigator.session.coordinator.get("tools") == {}
            assert navigator.bundle.mount_plan["hooks"] == []

            async def independent():
                try:
                    await asyncio.wait_for(received.wait(), 15)
                    # Both installed SDKs now use httpx2; older versions use
                    # httpx. Do not import a transport to seed its logger.
                    names = [n for n in ("httpcore2.http11", "httpcore.http11")
                             if n in logging.Logger.manager.loggerDict]
                    assert names
                    logging.getLogger(names[0]).debug("independent task log", extra={"keep": "unaltered"})
                finally:
                    release.set()

            task = asyncio.create_task(independent())
            try:
                try:
                    await navigator.decide({}, {"frames": []}, prompt, 20)
                except ShowrunError as exc:
                    public = exc.public()
                else:
                    raise AssertionError("Expected provider rejection")
                await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert public["code"] == "provider_invalid_request", public
            assert public["diagnostics"]["http_status"] == 400
            assert sum(method == "POST" for method, _ in requests) == 1
            assert navigator._dispatch_available is False
            assert logging.Logger.handle is original
            text = sink.text() + json.dumps(public)
            assert all(secret not in text for secret in (cookie, body, key, prompt)), "Guard leaked synthetic content"
            assert "independent task log" in text and "unaltered" in text
            # Negative control: same real SDK outside the guarded dispatch must
            # expose the synthetic header at DEBUG, proving the transport seam ran.
            sink.records.clear()
            try:
                if provider == "anthropic":
                    await navigator.provider.client.messages.create(
                        model=navigator.config["model"], max_tokens=32,
                        messages=[{"role": "user", "content": "negative control"}])
                else:
                    await navigator.provider.client.responses.create(
                        model=navigator.config["model"], input="negative control", max_output_tokens=32)
            except Exception:
                pass
            assert sum(method == "POST" for method, _ in requests) == 2
            assert cookie in sink.text(), "Negative control did not reach transport header logging"
            assert "receive_response_headers.complete" in sink.text()
            sink.records.clear()
        finally:
            await navigator.close()

    try:
        asyncio.run(run())
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        root.removeHandler(sink)
    assert logging.Logger.handle is original
    print(json.dumps({"status": "passed", "provider": provider, "network": "loopback_only"}))


if __name__ == "__main__":
    loopback_trial(sys.argv[1])