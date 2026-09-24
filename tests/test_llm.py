from __future__ import annotations

import json
from pathlib import Path

import pytest

from buildflow import llm
from buildflow.agent import ProjectAgent
from buildflow.ingestion import read_boq
from buildflow.llm import LLMClassificationError, classify_ambiguous_items
from buildflow.models import BOQItem, ProjectConfig


ROOT = Path(__file__).resolve().parent.parent
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def item(item_id: str, description: str, confidence: float, rate: float = 0.0, package: str = "unknown") -> BOQItem:
    return BOQItem(
        id=item_id,
        description=description,
        unit="Cum",
        quantity=10.0,
        rate=rate,
        amount=rate * 10.0,
        work_package=package,
        confidence=confidence,
        classifier="hybrid",
    )


def completion(classifications: list[dict]) -> dict:
    """A complete Groq chat-completion body whose message content is the structured JSON."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1757570000,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps({"classifications": classifications})},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 400, "completion_tokens": 60, "total_tokens": 460},
        "system_fingerprint": "fp_test",
        "x_groq": {"id": "req_test"},
    }


class FakeGroq:
    """Stands in for the HTTP call only: records requests and replays scripted (status, headers, body) replies."""

    def __init__(self, *responses: tuple[int, dict, dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, headers: dict, payload: dict) -> tuple[int, dict, dict]:
        self.requests.append((url, headers, payload))
        return self.responses.pop(0)


def sent_ids(payload: dict) -> list[str]:
    return [row["id"] for row in json.loads(payload["messages"][-1]["content"])["items"]]


@pytest.fixture(autouse=True)
def groq_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")


def test_only_low_confidence_rows_go_to_groq_without_rates():
    rows = [
        item("BOQ-001", "All kinds of soil", 0.50, rate=987.65),
        item("BOQ-002", "Kota stone flooring", 0.90, rate=1234.5),
    ]
    fake = FakeGroq((200, {}, completion([])))
    classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=fake)
    ((url, headers, payload),) = fake.requests
    assert url == GROQ_URL
    assert headers["Authorization"] == "Bearer gsk_test"
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert sent_ids(payload) == ["BOQ-001"]
    assert "987.65" not in json.dumps(payload)


def test_groq_classification_is_applied_to_the_ambiguous_row():
    rows = [item("BOQ-001", "All kinds of soil", 0.50, package="testing")]
    reply = completion([{"id": "BOQ-001", "work_package": "earthwork", "confidence": 0.9, "reason": "excavation sub-item"}])
    classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=FakeGroq((200, {}, reply)))
    assert (rows[0].work_package, rows[0].phase, rows[0].confidence, rows[0].classifier) == (
        "earthwork",
        "substructure",
        0.9,
        "hybrid+llm",
    )


def test_missing_groq_key_raises_so_the_agent_can_fall_back(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    fake = FakeGroq()
    with pytest.raises(LLMClassificationError, match="GROQ_API_KEY"):
        classify_ambiguous_items([item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=fake)
    assert fake.requests == []


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "BOQ-999", "work_package": "earthwork", "confidence": 0.9, "reason": "id the model invented"},
        {"id": "BOQ-001", "work_package": "excavation", "confidence": 0.9, "reason": "package outside the enum"},
        {"id": "BOQ-001", "work_package": "earthwork", "confidence": 1.7, "reason": "confidence out of range"},
    ],
    ids=["invented-id", "package-outside-enum", "confidence-above-1"],
)
def test_invalid_model_output_leaves_the_offline_classification(entry):
    rows = [item("BOQ-001", "All kinds of soil", 0.50, package="testing")]
    classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=FakeGroq((200, {}, completion([entry]))))
    assert (rows[0].work_package, rows[0].confidence, rows[0].classifier) == ("testing", 0.50, "hybrid")


def test_non_json_reply_raises_so_the_agent_can_fall_back():
    reply = completion([])
    reply["choices"][0]["message"]["content"] = "Sure! Here are the classifications:"
    with pytest.raises(LLMClassificationError):
        classify_ambiguous_items(
            [item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=FakeGroq((200, {}, reply))
        )


def rate_limited(seconds: str) -> tuple[int, dict, dict]:
    """A 429 as the transport returns it: raw error body text under "error"."""
    body = {"error": {"message": "Rate limit reached on tokens per minute (TPM)", "type": "tokens", "code": "rate_limit_exceeded"}}
    return 429, {"retry-after": seconds}, {"error": json.dumps(body)}


def test_many_ambiguous_rows_are_split_into_batches_each_sent_once():
    rows = [item(f"BOQ-{n:03d}", f"Row {n}", 0.50) for n in range(1, 2 * llm.BATCH_SIZE + 2)]
    fake = FakeGroq(*[(200, {}, completion([]))] * 3)
    classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=fake)
    batches = [sent_ids(payload) for _, _, payload in fake.requests]
    assert len(batches) == 3
    assert sorted(row_id for batch in batches for row_id in batch) == [row.id for row in rows]


def test_rate_limited_request_waits_for_retry_after_then_succeeds():
    rows = [item("BOQ-001", "All kinds of soil", 0.50, package="testing")]
    waits: list[float] = []
    reply = completion([{"id": "BOQ-001", "work_package": "earthwork", "confidence": 0.9, "reason": "excavation"}])
    fake = FakeGroq(rate_limited("7"), (200, {}, reply))
    classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=fake, sleep=waits.append)
    assert waits == [7.0]
    assert rows[0].work_package == "earthwork"


def test_persistent_rate_limit_gives_up_after_bounded_retries():
    fake = FakeGroq(*[rate_limited("1")] * 10)
    with pytest.raises(LLMClassificationError):
        classify_ambiguous_items(
            [item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=fake, sleep=lambda seconds: None
        )
    assert 1 < len(fake.requests) < 10


def test_llm_model_can_be_chosen_through_the_environment(monkeypatch):
    monkeypatch.setenv("BUILDFLOW_LLM_MODEL", "qwen/qwen3.8-27b")
    assert ProjectConfig().llm_model == "qwen/qwen3.8-27b"


def test_agent_trace_names_the_provider_and_model(monkeypatch):
    monkeypatch.setattr(llm, "_post_json", FakeGroq(*[(200, {}, completion([]))] * 5))
    config = ProjectConfig(typology="stp_tank", use_llm_fallback=True, llm_model="qwen/qwen3.8-27b")
    result = ProjectAgent().run(read_boq(ROOT / "data/samples/stp_boq.csv"), config)
    step = next(event for event in result.trace if event.tool == "llm_reader")
    assert (step.status, step.details) == ("completed", {"provider": "groq", "model": "qwen/qwen3.8-27b"})


def test_agent_skips_ai_reader_with_a_finding_when_groq_key_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    config = ProjectConfig(typology="stp_tank", use_llm_fallback=True)
    result = ProjectAgent().run(read_boq(ROOT / "data/samples/stp_boq.csv"), config)
    step = next(event for event in result.trace if event.tool == "llm_reader")
    assert step.status == "skipped"
    assert "LLM_FALLBACK_SKIPPED" in {finding.code for finding in result.findings}


def test_a_failed_later_batch_leaves_earlier_batches_unapplied():
    rows = [item(f"BOQ-{n:03d}", f"Row {n}", 0.50, package="testing") for n in range(1, llm.BATCH_SIZE + 2)]
    first = completion([{"id": "BOQ-001", "work_package": "earthwork", "confidence": 0.9, "reason": "excavation"}])
    fake = FakeGroq((200, {}, first), *[rate_limited("1")] * llm.MAX_ATTEMPTS)
    with pytest.raises(LLMClassificationError):
        classify_ambiguous_items(rows, "openai/gpt-oss-120b", transport=fake, sleep=lambda seconds: None)
    assert (rows[0].work_package, rows[0].classifier) == ("testing", "hybrid")


def test_retry_after_beyond_the_wait_cap_gives_up_without_sleeping():
    waits: list[float] = []
    fake = FakeGroq(rate_limited("3600"), (200, {}, completion([])))
    with pytest.raises(LLMClassificationError):
        classify_ambiguous_items(
            [item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=fake, sleep=waits.append
        )
    assert waits == []


def test_requests_carry_an_explicit_user_agent():
    """Groq's edge rejects urllib's default client signature with HTTP 403 (Cloudflare error 1010)."""
    fake = FakeGroq((200, {}, completion([])))
    classify_ambiguous_items([item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=fake)
    ((_, headers, _),) = fake.requests
    assert headers.get("User-Agent")


def test_http_date_retry_after_fails_as_a_classification_error_not_a_crash():
    """HTTP allows Retry-After as a date; the agent only catches LLMClassificationError."""
    status, _, body = rate_limited("1")
    fake = FakeGroq((status, {"retry-after": "Fri, 11 Sep 2026 07:28:00 GMT"}, body), (200, {}, completion([])))
    with pytest.raises(LLMClassificationError):
        classify_ambiguous_items(
            [item("BOQ-001", "All kinds of soil", 0.50)], "openai/gpt-oss-120b", transport=fake, sleep=lambda seconds: None
        )
