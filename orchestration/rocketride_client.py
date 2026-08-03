"""The only application boundary allowed to invoke RocketRide inference."""

from __future__ import annotations

import ast
import json
import re
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel
from rocketride import RocketRideClient
from rocketride.schema import Answer, Question

from contracts import TaskEnvelope

PIPELINE_PATH = "runbook.pipe"

ROLE_INSTRUCTIONS = {
    "diagnostician": (
        "For mode grounded_candidates, explain each supplied candidate using only "
        "its evidence, preserve every deterministic field, and return trace_id plus "
        "reasoning_by_hypothesis. For mode alert_only_guess, there is deliberately "
        "no graph context: make an honest low-confidence guess from only the alert "
        "and return trace_id plus guess={type, affected_entity, confidence, reasoning}."
    ),
    "remediator": (
        "Justify only the supplied action. Do not propose, rename, or replace the "
        'action. Return exactly a JSON object with keys "trace_id" and "reasoning".'
    ),
}


def _parse_json_text(value: str) -> dict[str, Any]:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # RocketRide 1.3 serializes expectJson answers using Python's dict repr.
        parsed = ast.literal_eval(text)
    if not isinstance(parsed, dict):
        raise ValueError(  # noqa: TRY004 - malformed response is a value error.
            "RocketRide response must be a JSON object"
        )
    return parsed


def _answer_value(value: Any) -> Any:
    if isinstance(value, Answer):
        return value.answer
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="json")
        return dumped.get("answer", dumped)
    if isinstance(value, dict) and set(value).issuperset({"answer"}):
        return value["answer"]
    return value


def _extract_answer(response: dict[str, Any]) -> Any:
    result_types = response.get("result_types", {})
    answer_key = next(
        (key for key, lane_type in result_types.items() if lane_type == "answers"),
        "answers",
    )
    answers = response.get(answer_key, [])
    if not answers:
        raise ValueError(f"RocketRide returned no answers under {answer_key!r}")
    return _answer_value(answers[0])


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    value = _extract_answer(response)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return _parse_json_text(value)
    raise ValueError(f"Unsupported RocketRide answer type: {type(value).__name__}")


class RunbookInference:
    """Long-lived Cloud pipeline connection; start once and reuse its token."""

    def __init__(self, pipeline_path: str = PIPELINE_PATH) -> None:
        self.pipeline_path = pipeline_path
        self.client = RocketRideClient()
        self.token: str | None = None

    async def __aenter__(self) -> Self:
        await self.client.connect()
        result = await self.client.use(
            filepath=self.pipeline_path,
            use_existing=True,
            pipelineTraceLevel="full",
            name="runbook-inference",
        )
        self.token = result["token"]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.client.disconnect()
        self.token = None

    async def invoke(self, envelope: TaskEnvelope) -> dict[str, Any]:
        if self.token is None:
            raise RuntimeError("Use RunbookInference as an async context manager")

        question = Question(expectJson=True, role=envelope.role)
        question.addInstruction("Contract", ROLE_INSTRUCTIONS[envelope.role])
        question.addContext(envelope.payload)
        question.addQuestion(
            json.dumps(
                {
                    "role": envelope.role,
                    "trace_id": envelope.trace_id,
                    "request": "Execute this task without changing deterministic fields.",
                },
                sort_keys=True,
            )
        )
        response = await self.client.chat(token=self.token, question=question)
        try:
            parsed = _extract_json(response)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            raw_answer = str(_extract_answer(response))
            if envelope.role == "remediator":
                parsed = {
                    "trace_id": envelope.trace_id,
                    "reasoning": raw_answer,
                }
            elif envelope.payload.get("mode") == "alert_only_guess":
                parsed = {
                    "trace_id": envelope.trace_id,
                    "guess": {
                        "type": "unknown",
                        "affected_entity": envelope.payload["alert"]["service"],
                        "confidence": 0.4,
                        "reasoning": raw_answer,
                    },
                }
            else:
                parsed = {
                    "trace_id": envelope.trace_id,
                    "reasoning_by_hypothesis": {
                        candidate["hypothesis_id"]: raw_answer
                        for candidate in envelope.payload.get("candidates", [])
                    },
                }
        if parsed.get("trace_id") != envelope.trace_id:
            raise ValueError("RocketRide response did not preserve trace_id")
        return parsed
