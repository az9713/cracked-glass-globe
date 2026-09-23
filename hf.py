"""Local, server-side Higgsfield SDK integration. Never logs credentials."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import cached_property
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

from dotenv import dotenv_values
import httpx
from higgsfield_client import SyncClient, SyncRequestController
from higgsfield_client.http.retry import NoRetry
from higgsfield_client.http.transport import HttpTransport

ROOT = Path(__file__).resolve().parent
PRODUCTION = "https://api.higgsfield.ai"
CATALOG = "https://open.higgsfield.ai/explore"
TERMINAL = {"completed", "failed", "nsfw", "canceled", "cancelled"}
CREDENTIAL_NAMES = ("HF_KEY", "HF_CREDENTIALS", "HIGGSFIELD_API_KEY")


class ConfigurationError(ValueError):
    pass


def environment():
    # Resolve relative to this project even when called from another directory.
    return {**dotenv_values(ROOT / ".env", interpolate=False), **os.environ}


def credentials(env):
    for name in CREDENTIAL_NAMES:
        if env.get(name):
            value = env[name].strip()
            break
    else:
        key, secret = env.get("HF_API_KEY"), env.get("HF_API_SECRET")
        if not key or not secret:
            raise ConfigurationError("Missing credentials. Set HIGGSFIELD_API_KEY=KEY_ID:KEY_SECRET in .env.")
        value = f"{key.strip()}:{secret.strip()}"
    if not re.fullmatch(r"[^\s:]+:[^\s:]+", value) or "YOUR_KEY" in value:
        raise ConfigurationError("Credentials must contain both KEY_ID and KEY_SECRET, separated by a colon. Value withheld.")
    return value


def base_url(env):
    value = env.get("HF_BASE_URL", PRODUCTION).rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".higgsfield.ai")
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path or parsed.query or parsed.fragment):
        raise ConfigurationError("HF_BASE_URL must be an HTTPS Higgsfield API origin specified in official model documentation.")
    return value


def model_id(value):
    value = value.strip().strip("/")
    if not value or not all(re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
                            for part in value.split("/")):
        raise ConfigurationError("Use a model endpoint ID from the console, not a URL or query string.")
    return value


def request_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ConfigurationError("Invalid request ID.")
    return value


def redact(value, env):
    """Redact credential values and their components from serialized output."""
    text = json.dumps(value, ensure_ascii=False, indent=2)
    secrets = set()
    for key, item in env.items():
        if item and re.search(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL", key, re.I):
            secrets.add(item)
            if key in CREDENTIAL_NAMES:
                secrets.update(item.split(":"))
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
    return text


class SafeClient(SyncClient):
    """Pin SDK internals to 0.2.0; never automatically retry a billable POST."""
    @cached_property
    def _client(self):
        def same_origin(request):
            if request.url.copy_with(path="/", query=None) != httpx.URL(self.base_url + "/"):
                raise ConfigurationError("Refused to send API credentials to a different origin.")

        return httpx.Client(
            base_url=self.base_url, timeout=self.timeout,
            headers={"Authorization": f"Key {self.api_key}",
                     "User-Agent": "higgsfield-client-py/0.2.0", "Content-Type": "application/json"},
            event_hooks={"request": [same_origin]},
        )

    @cached_property
    def _transport(self):
        return HttpTransport(self._client, NoRetry())

    def get_request_controller(self, identifier):
        # SDK 0.2.0 otherwise reconstructs request URLs on the production host.
        urls = SyncRequestController.build_urls(self.base_url, request_id(identifier))
        return SyncRequestController(identifier, self._transport, **urls)

    def snapshot(self, identifier):
        # Keep every model-specific output field; no image/video-only projection.
        response = self._client.get(f"/requests/{request_id(identifier)}/status")
        response.raise_for_status()
        return response.json()

    def wait(self, identifier, *, timeout=1800, interval=3):
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(interval) or interval <= 0:
            raise ConfigurationError("Timeout and polling interval must be positive finite numbers.")
        deadline = time.monotonic() + timeout
        failures = 0
        while time.monotonic() < deadline:
            try:
                data = self.snapshot(identifier)
                failures = 0
                if data.get("status") in TERMINAL:
                    return data
                delay = interval
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise
                failures += 1
                if failures >= 4:
                    raise
                delay = min(30, 2 ** failures) + random.random()
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
        raise TimeoutError("Local wait expired. The server job may still run; resume with its saved request ID.")

    def close(self):
        for name in ("_client", "_upload_client"):
            if name in self.__dict__:
                self.__dict__[name].close()


@contextmanager
def client(env=None):
    env = environment() if env is None else env
    sdk = SafeClient(api_key=credentials(env), base_url=base_url(env), timeout=60)
    try:
        yield sdk
    finally:
        sdk.close()


def input_arguments(path, prompt=None):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    except json.JSONDecodeError:
        raise ConfigurationError("Input file is not valid JSON; contents withheld.") from None
    if not isinstance(data, dict):
        raise ConfigurationError("Input must be a JSON object containing the model's parameters.")
    if prompt is not None:
        data["prompt"] = prompt
    if not data:
        raise ConfigurationError("Provide --input parameters.json or --prompt text.")
    return data


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.models = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        path = urlsplit(href).path
        match = re.fullmatch(r"/models/(.+)/(?:playground|api-reference)/?", path)
        if match:
            self.models.add(model_id(match[1]))


def discover(search=""):
    # Public catalog only: no credentials sent, no undocumented catalog API.
    response = httpx.get(CATALOG, timeout=30, follow_redirects=True)
    response.raise_for_status()
    parser = CatalogParser()
    parser.feed(response.text)
    if not parser.models:
        raise ConfigurationError("Catalog markup changed. Browse https://console.higgsfield.ai for current models.")
    return [{"model": item, "documentation": f"https://open.higgsfield.ai/models/{item}/api-reference"}
            for item in sorted(parser.models) if search.lower() in item.lower()]


def save(data, env, label):
    directory = ROOT / "outputs"
    directory.mkdir(exist_ok=True)
    path = directory / f"{label}-{uuid.uuid4().hex[:12]}.json"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(redact(data, env) + "\n")
    return str(path)


def error_message(exc, env):
    status = exc
    while status is not None:
        if isinstance(status, httpx.HTTPStatusError):
            code = status.response.status_code
            hints = {401: "Credentials rejected.", 403: "Access denied or insufficient credits.",
                     404: "Request or model unavailable to this account.", 422: "Check the model's input schema.",
                     423: "Model temporarily blocked.", 503: "Model not ready or disabled."}
            return f"HTTP {code}: {hints.get(code, 'API request failed.')} Check the console before repeating a submission."
        status = status.__cause__
    if isinstance(exc, (ConfigurationError, TimeoutError)):
        return json.loads(redact(str(exc), env))
    if isinstance(exc, httpx.TransportError):
        return "Network request failed. If submitting, acceptance is unknown: check the console before submitting again."
    if isinstance(exc, OSError):
        return "File operation failed. Check the input path and permissions. Details withheld."
    return "Operation failed. Error details withheld to protect credentials. Check the model documentation and console."


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Check setup; --online performs a read-only authentication check")
    doctor.add_argument("--online", action="store_true")
    models = sub.add_parser("models", help="Discover model links from the live public console")
    models.add_argument("--search", default="")
    docs = sub.add_parser("docs", help="Print model-specific documentation link")
    docs.add_argument("model")
    for name in ("run", "submit"):
        command = sub.add_parser(name, help="Submit a generation (billable); run also waits")
        command.add_argument("model")
        command.add_argument("--input")
        command.add_argument("--prompt")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--webhook", help="Public HTTPS completion webhook")
        if name == "run":
            command.add_argument("--timeout", type=float, default=1800)
    for name in ("status", "result", "cancel"):
        command = sub.add_parser(name)
        command.add_argument("request_id")
        if name == "result":
            command.add_argument("--timeout", type=float, default=1800)
    upload = sub.add_parser("upload", help="Upload a local reference file; prints its URL")
    upload.add_argument("file")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    env = environment()

    def emit(data):
        print(redact(data, env), flush=True)

    try:
        if args.command == "models":
            emit({"source": CATALOG, "note": "Discovery links, not an exhaustive list of variants or proof of account access.",
                  "models": discover(args.search)})
            return 0
        if args.command == "docs":
            emit({"documentation": f"https://open.higgsfield.ai/models/{model_id(args.model)}/api-reference"})
            return 0
        if hasattr(args, "timeout") and (not math.isfinite(args.timeout) or args.timeout <= 0):
            raise ConfigurationError("Timeout must be a positive finite number.")
        if args.command in {"run", "submit"}:
            model = model_id(args.model)
            data = input_arguments(args.input, args.prompt)
            if args.webhook:
                webhook = urlsplit(args.webhook)
                if webhook.scheme != "https" or not webhook.hostname or webhook.username or webhook.password:
                    raise ConfigurationError("Webhook must be a public HTTPS URL without embedded credentials.")
            if args.dry_run:
                emit({"dry_run": True, "base_url": base_url(env), "model": model, "input": data,
                      "note": "No API request sent. Model-specific schema and account access are not validated."})
                return 0
        with client(env) as sdk:
            if args.command == "doctor":
                report = {"credentials": "present; expected format", "base_url": sdk.base_url,
                          "authentication": "not checked", "generation_submitted": False}
                if args.online:
                    response = sdk._client.get("/marketing-studio/image/presets", params={"size": 1})
                    response.raise_for_status()
                    report["authentication"] = "accepted by the read-only preset endpoint"
                    report["model_access"] = "not tested; varies by model"
                emit(report)
            elif args.command in {"run", "submit"}:
                controller = sdk.submit(model, arguments=data, webhook_url=args.webhook)
                identifier = request_id(controller.request_id)
                # Announce the ID before writing files or starting any polling.
                emit({"request_id": identifier, "submission": "accepted"})
                receipt = {"request_id": identifier, "model": model, "base_url": sdk.base_url}
                emit({"receipt": save(receipt, env, "request")})
                if args.command == "run":
                    result = sdk.wait(identifier, timeout=args.timeout)
                    emit({"result": result, "saved": save(result, env, "result")})
                    return 0 if result.get("status") == "completed" else 2
            elif args.command == "status":
                emit(sdk.snapshot(args.request_id))
            elif args.command == "result":
                result = sdk.wait(request_id(args.request_id), timeout=args.timeout)
                emit({"result": result, "saved": save(result, env, "result")})
                return 0 if result.get("status") == "completed" else 2
            elif args.command == "cancel":
                sdk.cancel(request_id(args.request_id))
                emit({"request_id": args.request_id, "cancellation": "accepted"})
            elif args.command == "upload":
                path = Path(args.file).resolve()
                if path.name == ".env" or path.name.startswith(".env."):
                    raise ConfigurationError("Refused to upload an environment credential file.")
                emit({"url": sdk.upload_file(path)})
        return 0
    except KeyboardInterrupt:
        print("Stopped locally. Any accepted server job may still run; use its saved request ID to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(error_message(exc, env), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
