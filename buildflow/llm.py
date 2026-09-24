from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .models import BOQItem
from .taxonomy import TAXONOMY, infer_track


class LLMClassificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str


# Free-tier, OpenAI-compatible Chat Completions endpoint. A local Ollama/Gemma
# provider can later be added as another constant using the same request path.
GROQ = Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY")

BATCH_SIZE = 20  # rows per request, keeping each call well inside the free tier's tokens-per-minute budget
MAX_ATTEMPTS = 3  # per batch, when the provider answers 429 Too Many Requests
MAX_RETRY_WAIT_SECONDS = 60  # a longer Retry-After (e.g. an exhausted daily quota) fails fast instead of stalling

# (url, headers, JSON payload) -> (HTTP status, lower-cased headers, decoded JSON body)
Transport = Callable[[str, dict[str, str], dict], tuple[int, dict[str, str], dict]]

INSTRUCTIONS = (
    "You classify Indian construction BOQ line items. Choose only from the supplied "
    "work packages. Do not estimate quantities, rates, dates, or dependencies. Return "
    "a concise classification and confidence for every supplied id."
)


def _post_json(url: str, headers: dict[str, str], payload: dict) -> tuple[int, dict[str, str], dict]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, {key.lower(): value for key, value in response.headers.items()}, body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return exc.code, {key.lower(): value for key, value in (exc.headers or {}).items()}, {"error": detail}
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMClassificationError(f"Classification request failed: {exc}") from exc


def _request_payload(model: str, ambiguous: list[BOQItem], packages: list[str]) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "work_package": {"type": "string", "enum": packages},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "work_package", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["classifications"],
        "additionalProperties": False,
    }
    rows = [{"id": item.id, "description": item.description, "unit": item.unit} for item in ambiguous]
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": json.dumps({"allowed_work_packages": packages, "items": rows})},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "boq_classification", "strict": True, "schema": schema},
        },
    }


def _request_classifications(
    post: Transport, api_key: str, model: str, batch: list[BOQItem], packages: list[str], sleep: Callable[[float], object]
) -> list:
    url = f"{GROQ.base_url}/chat/completions"
    # An explicit User-Agent is required: Groq's edge rejects urllib's default signature (HTTP 403, error 1010).
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "buildflow/0.1"}
    payload = _request_payload(model, batch, packages)
    attempt = 1
    status, response_headers, raw = post(url, headers, payload)
    while status == 429 and attempt < MAX_ATTEMPTS:
        try:
            wait = float(response_headers.get("retry-after", 1))
        except ValueError:  # HTTP-date form; fail fast rather than parse dates for a fallback path
            break
        if wait > MAX_RETRY_WAIT_SECONDS:
            break
        sleep(wait)
        attempt += 1
        status, response_headers, raw = post(url, headers, payload)
    if status != 200:
        raise LLMClassificationError(f"Groq classification request failed: HTTP {status} {raw}")
    try:
        return json.loads(raw["choices"][0]["message"]["content"])["classifications"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMClassificationError(f"Groq returned an unreadable classification: {exc}") from exc


def _apply_classifications(batch: list[BOQItem], entries: list) -> None:
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
    phase_by_package = {taxon.key: taxon.phase for taxon in TAXONOMY}
    for item in batch:
        entry = by_id.get(item.id)
        if not entry:
            continue
        package, confidence = entry.get("work_package"), entry.get("confidence")
        # The model is advisory: accept only a known package at moderate-to-full
        # self-reported confidence. "unknown" is not a taxon, so it is never applied.
        if package not in phase_by_package or not isinstance(confidence, (int, float)) or not 0.55 <= confidence <= 1:
            continue
        item.work_package = package
        item.phase = phase_by_package[package]
        item.track = infer_track(item.description.lower(), package)
        item.confidence = round(float(confidence), 3)
        item.classifier = "hybrid+llm"
        item.evidence = [str(entry.get("reason", ""))[:180]]


def classify_ambiguous_items(
    items: list[BOQItem],
    model: str,
    transport: Transport | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> list[BOQItem]:
    """Classify only low-confidence rows through Groq's strict JSON-schema Chat Completions.

    BOQ text is sent externally only when the caller explicitly enables this function.
    """

    api_key = os.getenv(GROQ.key_env, "").strip()
    if not api_key:
        raise LLMClassificationError(f"{GROQ.key_env} is not set")
    ambiguous = [item for item in items if item.confidence < 0.68]
    packages = [taxon.key for taxon in TAXONOMY] + ["unknown"]
    post = transport or _post_json
    batches = [ambiguous[start : start + BATCH_SIZE] for start in range(0, len(ambiguous), BATCH_SIZE)]
    # Request every batch before applying any, so a failure leaves all rows on their offline classification.
    results = [(batch, _request_classifications(post, api_key, model, batch, packages, sleep)) for batch in batches]
    for batch, entries in results:
        _apply_classifications(batch, entries)
    return items
