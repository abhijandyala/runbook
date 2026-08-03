"""FastAPI boundary for the M3 LaserData-backed review UI."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from adapters import (
    SlackReadError,
    assemble_bridge_report,
    complaint_to_alert,
    connector_dry_run,
    connector_status,
    fetch_slack_messages,
    redact_complaint,
)
from adapters.config import slack_token
from agents.diagnostician import diagnose
from agents.remediator import enforce_action_policy, remediate
from agents.reviewer import (
    OUTCOME_TIMEOUT_SECONDS,
    OutcomeObservation,
    decide,
    effective_action_for,
    resolve,
    watch_for_outcome,
)
from contracts import (
    ActionProposal,
    Alert,
    BridgeComplaintRequest,
    BridgeReport,
    HypothesisSet,
    Resolution,
    ReviewerDecision,
)
from falkor.writes import write_resolution
from orchestration.guild_client import (
    GuildCoordinator,
    build_guild_coordinator,
)
from orchestration.rocketride_client import RunbookInference
from streams.iggy_client import IggyClient
from streams.stream_names import (
    TOPIC_ALERTS,
    TOPIC_HYPOTHESES,
    TOPIC_PROPOSALS,
    TOPIC_RESOLUTIONS,
)

logger = logging.getLogger(__name__)
INDEX_PATH = Path(__file__).resolve().with_name("index.html")
DEMO_DIST_PATH = Path(__file__).resolve().parent.parent / "web" / "dist"
SIMULATED_RECOVERY_DELAY_SECONDS = 0.25


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AlertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    metric: str
    value: float
    threshold: float
    severity: Literal["critical", "warning", "info"] = "warning"
    graph_enabled: bool = True


class AlertAccepted(BaseModel):
    alert_id: str


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    decision: Literal["approve", "reject", "modify"]
    reviewer_note: str | None = None
    modified_action: ActionProposal | None = None
    typed_confirmation: str | None = None


@dataclass(frozen=True)
class OwnedAlert:
    service: str
    graph_enabled: bool


class EventBroker:
    """Fan consumed topic records out to connected SSE clients."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[tuple[str, dict[str, object]]]] = set()

    async def broadcast(
        self,
        event: str,
        payload: BaseModel | dict[str, object],
    ) -> None:
        body = (
            payload.model_dump(mode="json")
            if isinstance(payload, BaseModel)
            else payload
        )
        item = (event, body)
        for queue in tuple(self._clients):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    async def stream(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, object]]] = asyncio.Queue(
            maxsize=100
        )
        self._clients.add(queue)
        try:
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                data = json.dumps(payload, separators=(",", ":"))
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            self._clients.discard(queue)


class M3Runtime:
    """Own cloud connections, durable workers, and pending review state."""

    def __init__(self, guild: GuildCoordinator | None = None) -> None:
        self.session_id = f"m3_{uuid4().hex[:12]}"
        self.events = EventBroker()
        self.streams: IggyClient | None = None
        self.inference: RunbookInference | None = None
        self.guild = build_guild_coordinator(guild)
        self.started = False
        self._stack: AsyncExitStack | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._owned_alerts: dict[str, OwnedAlert] = {}
        self._alert_history: dict[str, Alert] = {}
        self._hypothesis_history: dict[str, HypothesisSet] = {}
        self._proposal_history: dict[str, ActionProposal] = {}
        self._decision_history: dict[str, ReviewerDecision] = {}
        self._resolution_history: dict[str, Resolution] = {}
        self._bridge_history: dict[str, BridgeComplaintRequest] = {}
        self._pending_proposals: dict[str, ActionProposal] = {}
        self._owned_proposal_ids: set[str] = set()
        self._processed_proposal_ids: set[str] = set()
        self._submitted_decisions: dict[str, ReviewerDecision] = {}
        self._submitted_decision_requests: dict[str, DecisionRequest] = {}
        self._broadcast_resolution_ids: set[str] = set()
        self._decision_lock = asyncio.Lock()
        self._bridge_lock = asyncio.Lock()

    async def start(self) -> None:
        stack = AsyncExitStack()
        try:
            await self.guild.ensure_registered()
            streams = await stack.enter_async_context(IggyClient())
            await streams.ensure_topology()
            inference = await stack.enter_async_context(RunbookInference())
        except Exception:
            await stack.aclose()
            raise

        self._stack = stack
        self.streams = streams
        self.inference = inference
        self.started = True
        self._tasks = [
            asyncio.create_task(self._diagnosis_worker(), name="m3-diagnosis"),
            asyncio.create_task(self._remediation_worker(), name="m3-remediation"),
            asyncio.create_task(
                self._model_listener(TOPIC_ALERTS, "alert", Alert),
                name="m3-listen-alerts",
            ),
            asyncio.create_task(
                self._model_listener(
                    TOPIC_HYPOTHESES,
                    "hypotheses",
                    HypothesisSet,
                ),
                name="m3-listen-hypotheses",
            ),
            asyncio.create_task(
                self._proposal_listener(),
                name="m3-listen-proposals",
            ),
            asyncio.create_task(
                self._decision_listener(),
                name="m3-listen-decisions",
            ),
            asyncio.create_task(
                self._resolution_listener(),
                name="m4-listen-resolutions",
            ),
        ]

    async def stop(self) -> None:
        self.started = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self.streams = None
        self.inference = None

    def _reader_name(self, purpose: str) -> str:
        return f"{purpose}-{self.session_id}"

    def _is_owned_alert(self, alert: Alert) -> bool:
        return (
            alert.alert_id in self._owned_alerts
            and alert.annotations.get("m3_session_id") == self.session_id
        )

    async def create_alert(self, request: AlertRequest) -> str:
        alert_id = f"alrt_ui_{uuid4().hex[:12]}"
        alert = Alert(
            alert_id=alert_id,
            fired_at=_iso_now(),
            severity=request.severity,
            service=request.service,
            metric=request.metric,
            value=request.value,
            threshold=request.threshold,
            labels={"source": "m3-server"},
            annotations={
                "summary": "Alert submitted through the M3 browser API",
                "m3_session_id": self.session_id,
            },
        )
        await self._publish_owned_alert(
            alert,
            graph_enabled=request.graph_enabled,
        )
        return alert_id

    async def _publish_owned_alert(
        self,
        alert: Alert,
        *,
        graph_enabled: bool,
    ) -> None:
        """Publish through the one LaserData-owned alert ingress path."""

        streams = self._require_streams()
        self._owned_alerts[alert.alert_id] = OwnedAlert(
            service=alert.service,
            graph_enabled=graph_enabled,
        )
        self._alert_history[alert.alert_id] = alert
        try:
            await streams.publish(TOPIC_ALERTS, alert)
        except Exception:
            self._owned_alerts.pop(alert.alert_id, None)
            self._alert_history.pop(alert.alert_id, None)
            raise

    async def create_bridge_complaint(
        self,
        complaint: BridgeComplaintRequest,
    ) -> str:
        alert = complaint_to_alert(complaint, session_id=self.session_id)
        async with self._bridge_lock:
            if alert.alert_id in self._bridge_history:
                return alert.alert_id
            self._bridge_history[alert.alert_id] = redact_complaint(complaint)
            try:
                await self._publish_owned_alert(
                    alert,
                    graph_enabled=complaint.graph_enabled,
                )
            except Exception:
                self._bridge_history.pop(alert.alert_id, None)
                raise
        return alert.alert_id

    def get_bridge_report(self, alert_id: str) -> BridgeReport:
        complaint = self._bridge_history.get(alert_id)
        alert = self._alert_history.get(alert_id)
        if complaint is None or alert is None:
            raise LookupError("Complaint Bridge alert was not found")
        proposal = next(
            (
                item
                for item in reversed(tuple(self._proposal_history.values()))
                if item.alert_id == alert_id
            ),
            None,
        )
        guild = getattr(self, "guild", None)
        guild_mode = getattr(guild, "mode", "not-configured")
        return assemble_bridge_report(
            alert=alert,
            complaint=complaint,
            hypotheses=self._hypothesis_history.get(alert_id),
            proposal=proposal,
            decision=self._decision_history.get(alert_id),
            resolution=self._resolution_history.get(alert_id),
            guild_mode=guild_mode,
        )

    async def submit_decision(
        self,
        request: DecisionRequest,
    ) -> ReviewerDecision:
        streams = self._require_streams()
        async with self._decision_lock:
            submitted = self._submitted_decisions.get(request.proposal_id)
            if submitted is not None:
                original = self._submitted_decision_requests[request.proposal_id]
                if request != original:
                    raise ValueError(
                        "A different decision was already submitted for this proposal"
                    )
                return submitted.model_copy(deep=True)

            proposal = self._pending_proposals.get(request.proposal_id)
            if proposal is None:
                raise LookupError("Pending proposal was not found")

            modified_action = request.modified_action
            if modified_action is not None:
                if (
                    modified_action.proposal_id != proposal.proposal_id
                    or modified_action.alert_id != proposal.alert_id
                ):
                    raise ValueError(
                        "modified_action must preserve proposal_id and alert_id"
                    )
                modified_action = enforce_action_policy(modified_action)

            reviewer_decision = decide(
                proposal,
                decision=request.decision,
                modified_action=modified_action,
                reviewer_note=request.reviewer_note,
                typed_confirmation=request.typed_confirmation,
                guild_task_id=proposal.guild_task_id,
            )
            await self.guild.record_decision(reviewer_decision)
            await streams.publish(TOPIC_RESOLUTIONS, reviewer_decision)
            self._pending_proposals.pop(request.proposal_id, None)
            result = ReviewerDecision.model_validate(reviewer_decision)
            self._submitted_decision_requests[proposal.proposal_id] = (
                request.model_copy(deep=True)
            )
            self._submitted_decisions[proposal.proposal_id] = result.model_copy(
                deep=True
            )
            self._decision_history[proposal.alert_id] = result
            return result

    def _require_streams(self) -> IggyClient:
        if not self.started or self.streams is None:
            raise RuntimeError("M3 runtime is not connected")
        return self.streams

    def _require_inference(self) -> RunbookInference:
        if not self.started or self.inference is None:
            raise RuntimeError("M3 inference is not connected")
        return self.inference

    async def _diagnosis_worker(self) -> None:
        streams = self._require_streams()
        inference = self._require_inference()
        async for raw_alert in streams.subscribe(
            TOPIC_ALERTS,
            self._reader_name("diagnose-alerts"),
        ):
            try:
                alert = Alert.model_validate(raw_alert)
                owned = self._owned_alerts.get(alert.alert_id)
                if owned is None or not self._is_owned_alert(alert):
                    continue
                self._alert_history[alert.alert_id] = alert
                hypotheses = await diagnose(
                    alert,
                    inference,
                    graph_enabled=owned.graph_enabled,
                )
                guild_handoff_id = await self.guild.record_handoff(
                    "diagnostician",
                    "remediator",
                    alert.alert_id,
                    hypotheses.model_dump(mode="json"),
                )
                if self.guild.qualifying and guild_handoff_id is None:
                    raise RuntimeError(
                        "Qualifying Guild mode returned no handoff ID"
                    )
                hypotheses = hypotheses.model_copy(
                    update={"guild_handoff_id": guild_handoff_id}
                )
                await streams.publish(TOPIC_HYPOTHESES, hypotheses)
            except Exception:
                logger.exception("Failed to diagnose a consumed alert")

    async def _remediation_worker(self) -> None:
        streams = self._require_streams()
        inference = self._require_inference()
        async for raw_hypotheses in streams.subscribe(
            TOPIC_HYPOTHESES,
            self._reader_name("remediate-hypotheses"),
        ):
            try:
                hypotheses = HypothesisSet.model_validate(raw_hypotheses)
                owned = self._owned_alerts.get(hypotheses.alert_id)
                if owned is None:
                    continue
                self._hypothesis_history[hypotheses.alert_id] = hypotheses
                if (
                    self.guild.qualifying
                    and hypotheses.guild_handoff_id is None
                ):
                    raise RuntimeError(
                        "Qualifying Guild mode requires a diagnostician-to-"
                        "remediator handoff ID"
                    )
                proposal = await remediate(
                    hypotheses,
                    owned.service,
                    inference,
                    graph_enabled=owned.graph_enabled,
                )
                proposal = enforce_action_policy(proposal)
                guild_task_id = await self.guild.create_review_task(proposal)
                if self.guild.qualifying and guild_task_id is None:
                    raise RuntimeError(
                        "Qualifying Guild mode returned no review task ID"
                    )
                proposal = proposal.model_copy(
                    update={"guild_task_id": guild_task_id}
                )
                await streams.publish(TOPIC_PROPOSALS, proposal)
            except Exception:
                logger.exception("Failed to remediate consumed hypotheses")

    async def _model_listener(
        self,
        topic: str,
        event: Literal["alert", "hypotheses"],
        contract: type[Alert | HypothesisSet],
    ) -> None:
        streams = self._require_streams()
        async for raw_message in streams.subscribe(
            topic,
            self._reader_name(f"ui-{topic}"),
        ):
            try:
                message = contract.model_validate(raw_message)
                if isinstance(message, Alert):
                    if not self._is_owned_alert(message):
                        continue
                    self._alert_history[message.alert_id] = message
                elif message.alert_id not in self._owned_alerts:
                    continue
                else:
                    self._hypothesis_history[message.alert_id] = message
                await self.events.broadcast(event, message)
            except Exception:
                logger.exception("Failed to broadcast consumed %s record", topic)

    async def _proposal_listener(self) -> None:
        streams = self._require_streams()
        async for raw_proposal in streams.subscribe(
            TOPIC_PROPOSALS,
            self._reader_name("ui-proposals"),
        ):
            try:
                proposal = ActionProposal.model_validate(raw_proposal)
                if proposal.alert_id not in self._owned_alerts:
                    continue
                proposal = enforce_action_policy(proposal)
                self._proposal_history[proposal.proposal_id] = proposal
                self._pending_proposals[proposal.proposal_id] = proposal
                self._owned_proposal_ids.add(proposal.proposal_id)
                await self.events.broadcast("proposal", proposal)
            except Exception:
                logger.exception("Failed to broadcast consumed proposal")

    async def _decision_listener(self) -> None:
        streams = self._require_streams()
        async for raw_resolution in streams.subscribe(
            TOPIC_RESOLUTIONS,
            self._reader_name("ui-decisions"),
        ):
            try:
                decision = ReviewerDecision.model_validate(raw_resolution)
            except ValidationError:
                # Resolution records intentionally share this dict-encoded topic.
                continue
            if decision.proposal_id not in self._owned_proposal_ids:
                continue
            if decision.proposal_id in self._processed_proposal_ids:
                logger.info(
                    "Ignoring duplicate/stale decision for proposal %s",
                    decision.proposal_id,
                )
                continue
            self._processed_proposal_ids.add(decision.proposal_id)
            proposal = self._proposal_history.get(decision.proposal_id)
            if proposal is not None:
                self._decision_history[proposal.alert_id] = decision
            await self.events.broadcast("decision", decision)
            try:
                await self._process_decision(decision)
            except Exception:
                logger.exception(
                    "Failed to process consumed decision for proposal %s",
                    decision.proposal_id,
                )

    async def _process_decision(self, decision: ReviewerDecision) -> None:
        proposal = self._proposal_history.get(decision.proposal_id)
        if proposal is None:
            logger.warning(
                "Ignoring stale decision without proposal history: %s",
                decision.proposal_id,
            )
            return
        alert = self._alert_history.get(proposal.alert_id)
        if alert is None:
            logger.warning(
                "Ignoring stale decision without alert history: %s",
                decision.proposal_id,
            )
            return

        started = perf_counter()
        if decision.decision == "reject":
            resolution = resolve(
                proposal,
                decision,
                outcome="rejected",
                total_latency_ms=round((perf_counter() - started) * 1000),
            )
            await self._publish_and_persist(resolution, alert)
            return

        action = effective_action_for(proposal, decision)
        if action is None:
            raise RuntimeError("Approved decision did not produce an action")
        action = enforce_action_policy(action)

        action_payload: dict[str, object] = {
            "proposal_id": proposal.proposal_id,
            "alert_id": proposal.alert_id,
            "action_type": action.action_type,
            "action_target": action.action_target,
            "status": "dispatching",
            "simulated": True,
            "timestamp": _iso_now(),
            "message": "Simulation only; no production action was dispatched.",
        }
        logger.warning(
            "SIMULATED action dispatch for proposal %s: %s on %s; "
            "no production action executed",
            proposal.proposal_id,
            action.action_type,
            action.action_target,
        )
        await self.events.broadcast("action", action_payload)
        await asyncio.sleep(0)
        await self.events.broadcast(
            "action",
            {
                **action_payload,
                "status": "executed",
                "timestamp": _iso_now(),
                "message": (
                    "Simulated execution completed; "
                    "no production change occurred."
                ),
            },
        )

        observation = await self._watch_simulated_outcome(
            alert=alert,
            action=action,
        )
        resolution = resolve(
            proposal,
            decision,
            outcome=observation.outcome,
            total_latency_ms=round((perf_counter() - started) * 1000),
        )
        await self._publish_and_persist(resolution, alert)

    async def _watch_simulated_outcome(
        self,
        *,
        alert: Alert,
        action: ActionProposal,
    ) -> OutcomeObservation:
        streams = self._require_streams()
        await self.events.broadcast(
            "outcome",
            {
                "proposal_id": action.proposal_id,
                "alert_id": alert.alert_id,
                "status": "watching",
                "simulated": True,
                "timestamp": _iso_now(),
                "timeout_seconds": OUTCOME_TIMEOUT_SECONDS,
            },
        )
        alerts = streams.subscribe(
            TOPIC_ALERTS,
            self._reader_name(f"outcome-{action.proposal_id}"),
        )
        watcher = asyncio.create_task(
            watch_for_outcome(
                alerts,
                proposal_id=action.proposal_id,
                baseline_value=alert.value,
                threshold=alert.threshold,
                timeout_seconds=OUTCOME_TIMEOUT_SECONDS,
            )
        )
        try:
            await asyncio.sleep(SIMULATED_RECOVERY_DELAY_SECONDS)
            raw_value = action.action_params.get(
                "simulated_outcome_value",
                alert.threshold * 0.8,
            )
            try:
                simulated_value = float(raw_value)
            except (TypeError, ValueError):
                simulated_value = alert.threshold * 0.8
            recovery_alert = Alert(
                alert_id=f"alrt_sim_{uuid4().hex[:12]}",
                fired_at=_iso_now(),
                severity="info",
                service=alert.service,
                metric=alert.metric,
                value=simulated_value,
                threshold=alert.threshold,
                labels={"source": "simulated-post-action"},
                annotations={
                    "summary": (
                        "Synthetic recovery signal; not production telemetry"
                    ),
                    "simulated": "true",
                    "proposal_id": action.proposal_id,
                    "original_alert_id": alert.alert_id,
                },
            )
            logger.info(
                "Publishing correlated simulated recovery alert %s for %s",
                recovery_alert.alert_id,
                action.proposal_id,
            )
            await streams.publish(TOPIC_ALERTS, recovery_alert)
            observation = await watcher
        except BaseException:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            raise

        await self.events.broadcast(
            "outcome",
            {
                "proposal_id": action.proposal_id,
                "alert_id": alert.alert_id,
                "status": observation.status,
                "outcome": observation.outcome,
                "observed_value": (
                    observation.alert.value if observation.alert else None
                ),
                "threshold": alert.threshold,
                "simulated": True,
                "timestamp": _iso_now(),
                "timeout_seconds": OUTCOME_TIMEOUT_SECONDS,
            },
        )
        return observation

    def _root_cause_for(self, alert_id: str) -> str:
        hypotheses = self._hypothesis_history.get(alert_id)
        if hypotheses is None or not hypotheses.hypotheses:
            return "unknown"
        return hypotheses.hypotheses[0].type

    async def _publish_and_persist(
        self,
        resolution: Resolution,
        alert: Alert,
    ) -> None:
        streams = self._require_streams()
        incident_id = await asyncio.to_thread(
            write_resolution,
            alert,
            resolution,
            root_cause=self._root_cause_for(alert.alert_id),
        )
        if incident_id != resolution.incident_id:
            raise RuntimeError(
                f"FalkorDB returned unexpected incident id {incident_id}"
            )
        logger.info(
            "Persisted and fresh-query verified final resolution %s",
            resolution.incident_id,
        )
        await streams.publish(TOPIC_RESOLUTIONS, resolution)

    async def _resolution_listener(self) -> None:
        streams = self._require_streams()
        async for raw_resolution in streams.subscribe(
            TOPIC_RESOLUTIONS,
            self._reader_name("ui-final-resolutions"),
        ):
            try:
                resolution = Resolution.model_validate(raw_resolution)
            except ValidationError:
                # ReviewerDecision records intentionally share this topic.
                continue
            proposal_id = resolution.reviewer_decision.proposal_id
            if proposal_id not in self._owned_proposal_ids:
                continue
            if resolution.incident_id in self._broadcast_resolution_ids:
                logger.info(
                    "Ignoring duplicate/stale final resolution %s",
                    resolution.incident_id,
                )
                continue
            self._broadcast_resolution_ids.add(resolution.incident_id)
            self._resolution_history[resolution.alert_id] = resolution
            await self.events.broadcast("resolution", resolution)
            if resolution.alert_id in self._bridge_history:
                await self.events.broadcast(
                    "bridge_report",
                    self.get_bridge_report(resolution.alert_id),
                )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = M3Runtime()
    await runtime.start()
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="Motion Meets Memory M3", lifespan=lifespan)


@app.get("/", response_class=FileResponse)
async def dashboard() -> FileResponse:
    return FileResponse(INDEX_PATH, media_type="text/html")


def _sponsor_health(runtime: M3Runtime) -> dict[str, str | bool]:
    if not runtime.started:
        raise HTTPException(status_code=503, detail="M3 runtime is not connected")
    result: dict[str, str | bool] = {"status": "ok"}
    guild = getattr(runtime, "guild", None)
    if guild is not None:
        result.update(
            guild_mode=guild.mode,
            guild_qualifying=guild.qualifying,
        )
    return result


@app.get("/health")
async def health(request: Request) -> dict[str, str | bool]:
    runtime: M3Runtime = request.app.state.runtime
    return _sponsor_health(runtime)


@app.get("/bridge/health")
async def bridge_health(request: Request) -> dict[str, object]:
    runtime: M3Runtime = request.app.state.runtime
    return {
        "sponsor_health": _sponsor_health(runtime),
        **connector_status(),
        "connector_dry_run": connector_dry_run(),
    }


@app.get("/bridge/slack/messages")
async def bridge_slack_messages(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, str]]:
    token = slack_token()
    if not token:
        raise HTTPException(status_code=503, detail="SLACK_TOKEN is not configured")
    try:
        return await fetch_slack_messages(token=token, limit=limit)
    except SlackReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@app.post("/bridge/complaint", response_model=AlertAccepted)
async def create_bridge_complaint(
    payload: BridgeComplaintRequest,
    request: Request,
) -> AlertAccepted:
    runtime: M3Runtime = request.app.state.runtime
    try:
        alert_id = await runtime.create_bridge_complaint(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AlertAccepted(alert_id=alert_id)


@app.get("/bridge/report/{alert_id}", response_model=BridgeReport)
async def bridge_report(alert_id: str, request: Request) -> BridgeReport:
    runtime: M3Runtime = request.app.state.runtime
    try:
        return runtime.get_bridge_report(alert_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    runtime: M3Runtime = request.app.state.runtime
    return StreamingResponse(
        runtime.events.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/alerts", response_model=AlertAccepted)
async def create_alert(payload: AlertRequest, request: Request) -> AlertAccepted:
    runtime: M3Runtime = request.app.state.runtime
    try:
        alert_id = await runtime.create_alert(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AlertAccepted(alert_id=alert_id)


@app.post("/decisions", response_model=ReviewerDecision)
async def submit_decision(
    payload: DecisionRequest,
    request: Request,
) -> ReviewerDecision:
    runtime: M3Runtime = request.app.state.runtime
    try:
        return await runtime.submit_decision(payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/demo", response_class=FileResponse, include_in_schema=False)
async def demo_index() -> FileResponse:
    return FileResponse(DEMO_DIST_PATH / "index.html", media_type="text/html")


app.mount(
    "/demo",
    StaticFiles(directory=DEMO_DIST_PATH, html=True),
    name="demo",
)
