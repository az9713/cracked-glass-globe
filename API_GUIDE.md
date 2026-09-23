# Higgsfield API workspace

A local command-line client and reusable Python integration built on the **official `higgsfield-client` SDK**, pinned to 0.2.0. Pass any model endpoint ID and its JSON parameters. There is no fixed model allowlist, and model-specific output fields are preserved.

## Ready to use

Run `uv sync --locked` in this folder first. `uv run` then uses the locked dependencies.

```powershell
uv run python hf.py doctor
uv run python hf.py doctor --online
uv run python hf.py models --search seedance
uv run python hf.py docs marketing-studio/image
```

`doctor` checks credential format locally without printing the key. `doctor --online` makes a read-only request to the documented Marketing Studio preset endpoint. Success verifies authentication for that endpoint, not access to every model. It does not generate content.

Copy `.env.example` to `.env` and insert your own credentials privately. Supported credential names, in priority order: `HF_KEY`, `HF_CREDENTIALS`, `HIGGSFIELD_API_KEY`, then the pair `HF_API_KEY` and `HF_API_SECRET`. Each combined value must have the form `KEY_ID:KEY_SECRET`. Process environment values override matching `.env` variables. Avoid defining multiple credential aliases with different values.

The `.env` path is always resolved relative to this project. Keep it private. `.gitignore` excludes credentials, the local environment, and generated receipts/results. `.env.example` contains placeholders only.

## Generate with any model

1. Find a model in the [official console](https://console.higgsfield.ai).
2. Open its **API** tab and copy the exact model ID and required input fields.
3. Put those fields into a JSON file. Pass that file to `run` or `submit`.

Preview a request locally:

```powershell
uv run python hf.py run marketing-studio/image --input examples/marketing-studio.json --dry-run
```

Submit and wait for an image (this spends API credits):

```powershell
uv run python hf.py run marketing-studio/image --input examples/marketing-studio.json
```

Submit video generation:

```powershell
uv run python hf.py run bytedance/seedance-2.0/text-to-video --input examples/seedance-2.0.json
uv run python hf.py run bytedance/seedance-2.5/text-to-video --input examples/seedance-2.5.json
```

Generic form:

```powershell
uv run python hf.py run VENDOR/MODEL/VARIANT --input parameters.json
```

`--prompt "Your prompt"` supplies or overrides the top-level prompt. Other fields, including nested arrays and objects, pass through unchanged. A dry run validates JSON and endpoint syntax, not the selected model's schema, pricing, availability, or your access. Generation is billable when you run without `--dry-run`.

The `models` command reads public model links from the current console. It is a discovery aid: some variants only appear on a model's page. An absent listing does not prevent using an exact documented endpoint. Public listings also do not guarantee account access. The shared OpenAPI file is **not** an exhaustive model catalog.

## Interrupted connections and long jobs

```powershell
# Submit once, return immediately:
uv run python hf.py submit VENDOR/MODEL/VARIANT --input parameters.json

# Use the request ID printed by submit/run:
uv run python hf.py status REQUEST_ID
uv run python hf.py result REQUEST_ID --timeout 1800
uv run python hf.py cancel REQUEST_ID
```

Each accepted submission prints its ID and saves a receipt under `outputs/` before polling. `run` and `result` save the full terminal JSON response there. Media URLs are in that response; media files are not automatically downloaded. Preserve wanted media promptly according to the provider's retention policy.

Default local wait: 30 minutes; HTTP timeout: 60 seconds per network phase. A local timeout or Ctrl+C does not cancel the server job. `result` resumes waiting without submitting again. Failed, moderated, and cancelled jobs return exit code 2. Other errors return 1. Cancellation is subject to the API's queue rules.

Generation POST requests are never automatically retried. After a submission timeout, check the console before repeating it: the server may already have accepted and charged the job even if no ID reached your computer. Polling retries transient read failures with backoff, with at most four consecutive attempts and the local wait deadline.

Optional completion webhook:

```powershell
uv run python hf.py submit VENDOR/MODEL/VARIANT --input parameters.json --webhook https://your-server.example/higgsfield
```

This passes a callback URL to the provider; it does not create a webhook receiver. Follow the official webhook documentation when implementing the receiver.

## Reference images, video, or audio

```powershell
uv run python hf.py upload path/to/reference.png
```

The command uploads the file and prints its public media URL. Put that URL into the input field specified by the selected model's documentation (for example `image_urls` for Marketing Studio editing). The input schema varies by model. API credentials are not sent to the storage upload host. Environment files are refused by the upload command.

## Use from Python

```python
from hf import client

with client() as api:
    job = api.submit("marketing-studio/image", arguments={
        "prompt": "A ceramic teapot in morning light",
        "resolution": "2k",
        "aspect_ratio": "3:2",
        "enhance_prompt": False,
    })
    print(job.request_id)  # Save this before waiting.
    result = api.wait(job.request_id, timeout=1800)
    print(result)
```

The library interface does not automatically save receipts or redact your own prints; those protections are provided by the command-line wrapper. Do not print credentials or raw exception objects in your own scripts. `api.upload_file`, `api.cancel`, and `api.snapshot` are also available.

Production defaults to `https://api.higgsfield.ai`. If official model documentation explicitly specifies another Higgsfield API host, set `HF_BASE_URL` in `.env`. Use the same host when resuming a request; the receipt records it. The client does not silently switch production/preview environments. A nonproduction host has not been verified against this account.

## Reinstall and test

```powershell
uv sync --locked
uv run pytest -q
```

Alternatively use `.venv\Scripts\python.exe hf.py ...` after installation. Tests mock the network: they cover arbitrary model payloads, credential redaction, origin restrictions, uploads, resume behavior, polling deadlines, terminal failures, and avoiding duplicate submissions.

## Official references checked September 23, 2026

- [SDK documentation](https://docs.higgsfield.ai/docs/how-to/sdk)
- [Official Python SDK source](https://github.com/higgsfield-ai/higgsfield-client)
- [Authentication](https://docs.higgsfield.ai/docs/authentication)
- [Model discovery and source priority](https://docs.higgsfield.ai/docs/llms.txt)
- [Errors and retry policy](https://docs.higgsfield.ai/docs/concepts/errors)
- [Webhooks](https://docs.higgsfield.ai/docs/how-to/webhooks)
- [Marketing Studio Image inputs](https://open.higgsfield.ai/models/marketing-studio/image/api-reference)
- [Seedance 2.0 inputs](https://open.higgsfield.ai/models/bytedance/seedance-2.0/text-to-video/api-reference)
- [Seedance 2.5 inputs](https://open.higgsfield.ai/models/bytedance/seedance-2.5/text-to-video/api-reference)

Implementation note: the SDK's transport retry default applies to POSTs, and its request-ID controller defaults to the production host. The small `SafeClient` subclass explicitly overrides those behaviors. Dependencies are locked and these overrides are tested; review them when upgrading the SDK.

## Limits of this example client

This is a small local tool, not a production service. `snapshot` reconstructs a status URL on the configured origin; this worked in the recorded run, but the official lifecycle documentation recommends persisting and using the returned status URL. The origin guard deliberately rejects other hosts. A production implementation needs an explicitly reviewed provider-host allowlist and durable storage of returned request URLs. Do not remove the guard to follow arbitrary URLs with credentials.

The official billing documentation also describes `POST /estimate/{model}` for an authenticated cost estimate. This wrapper does not expose that endpoint yet; the earlier price discussion used public rate calculations, not an account quote or invoice. A dry run is not a price estimate. See the [billing documentation](https://docs.higgsfield.ai/docs/concepts/billing-and-retention) before a new paid run.
