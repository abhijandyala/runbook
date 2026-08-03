import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import type {
  ActionPreview,
  ActionProposal,
  Alert,
  BridgeHealth,
  BridgeReport,
  ConnectorAction,
  ConnectorActionStatus,
  HypothesisSet,
  Resolution,
  SlackMessage,
  Tab
} from "./contracts";
import { useRunbook } from "./store";
import { GitHubBrand, LinearBrand, NavIcon, RunbookMark, SlackBrand } from "./icons";
import { redactSecrets } from "./redaction";

const NAV: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "inbox", label: "Inbox" },
  { id: "approvals", label: "Approvals" },
  { id: "memory", label: "Memory" },
  { id: "settings", label: "Settings" }
];

function Badge({
  children,
  tone = "neutral"
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "purple";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty">
      <div className="empty-mark">·</div>
      <div className="empty-title">{title}</div>
      <div className="empty-detail">{detail}</div>
    </div>
  );
}

function ErrorBanner() {
  const { error } = useRunbook();
  if (!error) return null;
  return (
    <div className="error-banner">
      <strong>Backend request failed</strong>
      <span>{error}</span>
    </div>
  );
}

function healthFlag(health: BridgeHealth | null, name: string): boolean | null {
  if (!health) return null;
  const alias = name === "slack_connected" ? health.slack_configured : undefined;
  const direct = health[name] ?? alias;
  if (typeof direct === "boolean") return direct;
  const shortName = name.replace(/_(connected|configured)$/, "");
  const nested = health.integrations?.[shortName];
  if (typeof nested === "boolean") return nested;
  if (typeof nested === "string") return ["connected", "configured", "ok", "ready"].includes(nested.toLowerCase());
  return null;
}

function liveWritesEnabled(health: BridgeHealth | null): boolean {
  return health?.connector_live_writes_enabled === true;
}

function safeExternalUrl(raw?: string | null): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function privateText(value: string): string {
  return redactSecrets(value);
}

function compactTime(raw?: string): string {
  if (!raw) return "time unavailable";
  const numeric = Number(raw);
  const date = Number.isFinite(numeric) && !raw.includes("-")
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(raw);
  return Number.isNaN(date.valueOf())
    ? raw
    : new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }).format(date);
}

function messageChannel(message: SlackMessage) {
  return message.channel_name ?? message.channel ?? "channel unavailable";
}

function messageUser(message: SlackMessage) {
  return message.user_name ?? message.user ?? "Slack user";
}

function Sidebar({ active, setActive }: { active: Tab; setActive: (tab: Tab) => void }) {
  const { proposals, health, healthState } = useRunbook();
  const slack = healthFlag(health, "slack_connected");
  return (
    <aside className="sidebar">
      <div className="workspace-head">
        <RunbookMark />
        <div>
          <div className="product-name">runbook</div>
          <div className="workspace-name">TestTeam</div>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${active === item.id ? "active" : ""}`}
            onClick={() => setActive(item.id)}
          >
            <NavIcon name={item.id} />
            <span>{item.label}</span>
            {item.id === "approvals" && proposals.length > 0 && (
              <span className="nav-badge">{proposals.length}</span>
            )}
          </button>
        ))}
        <div className="nav-label">Workspace</div>
        <div className="project-row">
          <span className="project-folder">⌁</span>
          <span>Incident Command</span>
        </div>
        <div className="nav-label">Connected apps</div>
        <div className="project-row">
          <SlackBrand className="w-4 h-4" />
          <span>Slack</span>
          <span className={`status-dot ${slack === true ? "online" : slack === false ? "offline" : ""}`} />
        </div>
      </nav>
      <div className="sidebar-foot">
        <div className="runtime-line">
          <span className={`status-dot ${healthState === "ready" ? "online" : healthState === "error" ? "offline" : ""}`} />
          {healthState === "loading" ? "Checking bridge…" : healthState === "ready" ? "Bridge reachable" : "Bridge unavailable"}
        </div>
        <div className="user-chip">
          <span className="avatar">A</span>
          <span><strong>Abhi</strong><small>TestTeam operator</small></span>
        </div>
      </div>
    </aside>
  );
}

function TopBar({ active }: { active: Tab }) {
  const { streamConnected, running } = useRunbook();
  return (
    <header className="topbar">
      <div>
        <h1>{NAV.find((item) => item.id === active)?.label}</h1>
        <span className="crumb">Runbook Incident Command</span>
      </div>
      <div className="top-status">
        <span className={`status-dot ${streamConnected ? "online" : "offline"}`} />
        {running ? "Pipeline active" : streamConnected ? "Event stream connected" : "Event stream offline"}
      </div>
    </header>
  );
}

function WriteModeBanner() {
  const { health } = useRunbook();
  const live = liveWritesEnabled(health);
  return (
    <div className={`write-mode-banner ${live ? "live" : "dry"}`} role={live ? "alert" : "status"}>
      <strong>{live ? "LIVE WRITES ENABLED" : "DRY RUN"}</strong>
      <span>
        {live
          ? "Approvals can create Linear, GitHub, and Slack records. Verify every target before approving."
          : "Connector actions are simulated. No external connector write is authorized."}
      </span>
    </div>
  );
}

function SlackMessageRow({ message }: { message: SlackMessage }) {
  const { runMessage, running, selectedMessageId } = useRunbook();
  const active = selectedMessageId === message.id;
  return (
    <div className={`message-row ${active ? "selected" : ""}`}>
      <div className="message-avatar">{messageUser(message).slice(0, 1).toUpperCase()}</div>
      <div className="message-copy">
        <div className="message-meta">
          <strong>{messageUser(message)}</strong>
          <span>#{messageChannel(message)}</span>
          <span>{compactTime(message.timestamp ?? message.ts)}</span>
        </div>
        <p>{message.text}</p>
      </div>
      <button
        className="button primary compact"
        disabled={running}
        onClick={() => void runMessage(message).catch(() => undefined)}
      >
        {active && running ? "Running…" : "Run"}
      </button>
    </div>
  );
}

function SlackSourceCard() {
  const { health, messages, messagesState, refreshMessages } = useRunbook();
  const connected = healthFlag(health, "slack_connected");
  return (
    <section className="card slack-card">
      <div className="card-head">
        <div className="integration-title">
          <span className="integration-icon"><SlackBrand /></span>
          <div>
            <h2>Slack incident intake</h2>
            <p>Real messages returned by the configured bridge</p>
          </div>
        </div>
        <div className="head-actions">
          <Badge tone={connected === true ? "good" : connected === false ? "bad" : "neutral"}>
            {connected === true ? "CONNECTED" : connected === false ? "NOT CONNECTED" : "STATUS UNKNOWN"}
          </Badge>
          <button className="button ghost compact" onClick={() => void refreshMessages()}>Refresh</button>
        </div>
      </div>
      <div className="message-list">
        {messagesState === "loading" && <Empty title="Loading Slack messages" detail="Waiting for GET /bridge/slack/messages." />}
        {messagesState === "error" && <Empty title="Slack messages unavailable" detail="The bridge did not return message data. Use manual intake below." />}
        {messagesState === "ready" && messages.length === 0 && <Empty title="No Slack messages" detail="The connected bridge returned an empty list; no demo messages were fabricated." />}
        {messages.map((message) => <SlackMessageRow key={message.id} message={message} />)}
      </div>
    </section>
  );
}

function ManualComplaint() {
  const { runManualComplaint, running } = useRunbook();
  const [text, setText] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    void runManualComplaint(value).then(() => setText("")).catch(() => undefined);
  };
  return (
    <form className="manual-form" onSubmit={submit}>
      <div>
        <strong>Manual complaint fallback</strong>
        <span>Submit incident text when Slack is empty or unavailable.</span>
      </div>
      <textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="Example: Checkout latency spiked after the latest deploy…" rows={2} />
      <button className="button primary" disabled={running || !text.trim()}>{running ? "Pipeline active" : "Run complaint"}</button>
    </form>
  );
}

type StageStatus = "waiting" | "active" | "done";

function AgentPipeline() {
  const {
    selectedAlertId,
    alerts,
    hypotheses,
    proposals,
    decisions,
    actions,
    connectorActions,
    resolutions,
    running,
    health
  } = useRunbook();
  const proposal = proposals.find((item) => item.alert_id === selectedAlertId);
  const knownProposalIds = new Set(
    [...proposals].filter((item) => item.alert_id === selectedAlertId).map((item) => item.proposal_id)
  );
  const resolution = resolutions.find((item) => item.alert_id === selectedAlertId);
  if (resolution?.reviewer_decision.proposal_id) knownProposalIds.add(resolution.reviewer_decision.proposal_id);
  const hasDecision = decisions.some((item) => knownProposalIds.has(item.proposal_id)) || Boolean(resolution);
  const hasAction = actions.some((item) => item.alert_id === selectedAlertId);
  const hasConnectorAction = connectorActions.some(
    (item) => !item.alert_id || item.alert_id === selectedAlertId
  );
  const hasAlert = alerts.some((item) => item.alert_id === selectedAlertId);
  const hasHypotheses = hypotheses.some((item) => item.alert_id === selectedAlertId);

  const stages: { title: string; detail: string; status: StageStatus }[] = [
    { title: "Slack / manual intake", detail: "Bridge complaint", status: hasAlert ? "done" : running ? "active" : "waiting" },
    { title: "Diagnostician", detail: "Evidence + hypotheses", status: hasHypotheses ? "done" : hasAlert ? "active" : "waiting" },
    { title: "Remediator", detail: "Safe proposal", status: proposal || hasDecision ? "done" : hasHypotheses ? "active" : "waiting" },
    { title: "Human gate", detail: proposal ? "Approval required" : "Reviewer decision", status: hasDecision ? "done" : proposal ? "active" : "waiting" },
    {
      title: liveWritesEnabled(health) ? "Connector writes" : "Action preview",
      detail: liveWritesEnabled(health) ? "SSE / report status only" : "SIMULATED only",
      status: hasAction || hasConnectorAction ? "done" : hasDecision ? "active" : "waiting"
    },
    { title: "FalkorDB memory", detail: "Final incident ID", status: resolution ? "done" : hasAction || hasConnectorAction ? "active" : "waiting" }
  ];

  return (
    <section className="card pipeline-card">
      <div className="card-head">
        <div><h2>Agent pipeline</h2><p>{selectedAlertId ? `Alert ${selectedAlertId}` : "Select and run an intake item to begin"}</p></div>
        <Badge tone={running ? "warn" : resolution ? "good" : "neutral"}>{running ? "IN PROGRESS" : resolution ? "RESOLVED" : "IDLE"}</Badge>
      </div>
      <div className="pipeline-grid">
        {stages.map((stage, index) => (
          <div className={`pipeline-node ${stage.status}`} key={stage.title}>
            <div className="node-top"><span className="node-index">{index + 1}</span><span className="node-state">{stage.status}</span></div>
            <strong>{stage.title}</strong><small>{stage.detail}</small>
            {index < stages.length - 1 && <span className="connector-line" />}
          </div>
        ))}
      </div>
      {!selectedAlertId && <div className="pipeline-note">No backend run has been observed in this browser session.</div>}
    </section>
  );
}

function renderBrief(report?: BridgeReport): ReactNode {
  if (!report?.evidence_brief) return null;
  const brief = report.evidence_brief;
  if (typeof brief === "string") return <p>{brief}</p>;
  if (Array.isArray(brief)) return <ul>{brief.map((item) => <li key={item}>{item}</li>)}</ul>;
  const record = brief as Record<string, unknown>;
  return (
    <div className="brief-item">
      <strong>{String(record.summary ?? "Evidence brief")}</strong>
      <span>
        {String(record.current_stage ?? "stage unavailable")}
        {record.leading_hypothesis ? ` · ${String(record.leading_hypothesis)}` : ""}
        {Array.isArray(record.evidence) ? ` · ${record.evidence.length} evidence records` : ""}
      </span>
      {record.complaint_text ? <p>{String(record.complaint_text)}</p> : null}
    </div>
  );
}

function collectPreviews(report?: BridgeReport): ActionPreview[] {
  if (!report) return [];
  if (Array.isArray(report.action_previews)) return report.action_previews;
  const values: ActionPreview[] = [];
  const add = (provider: string, value: unknown) => {
    if (value && typeof value === "object") values.push({ provider, ...(value as Record<string, unknown>) });
  };
  add("linear", report.linear_preview);
  add("github", report.github_preview);
  add("slack", report.slack_preview);
  if (report.action_previews && !Array.isArray(report.action_previews)) {
    Object.entries(report.action_previews).forEach(([provider, value]) => add(provider, value));
  }
  return values;
}

function PreviewIcon({ provider }: { provider: string }) {
  if (provider.toLowerCase().includes("linear")) return <LinearBrand />;
  if (provider.toLowerCase().includes("github")) return <GitHubBrand />;
  return <SlackBrand />;
}

function previewDetail(preview: ActionPreview): string {
  if (preview.body) return preview.body;
  if (preview.target) return preview.target;
  if (preview.status) return preview.status;
  if (typeof preview.text === "string") return preview.text;
  if (preview.payload && typeof preview.payload === "object") {
    const payload = preview.payload as Record<string, unknown>;
    return String(payload.title ?? payload.description ?? payload.summary ?? "Structured dry-run payload ready");
  }
  return "Dry-run preview payload available";
}

function EvidenceAndPreviews() {
  const { selectedAlertId, reports, hypotheses, health } = useRunbook();
  const report = selectedAlertId ? reports[selectedAlertId] : undefined;
  const hypothesisSet = hypotheses.find((item) => item.alert_id === selectedAlertId);
  const previews = collectPreviews(report);
  const evidenceCount = hypothesisSet?.hypotheses.reduce((sum, hypothesis) => sum + hypothesis.evidence.length, 0) ?? 0;
  const live = liveWritesEnabled(health);
  return (
    <div className="evidence-preview-grid">
      <section className="card">
        <div className="card-head"><div><h2>Evidence brief</h2><p>Bridge report plus grounded evidence</p></div><Badge>{evidenceCount} SOURCES</Badge></div>
        <div className="brief-body">
          {renderBrief(report)}
          {!report?.evidence_brief && hypothesisSet && (
            hypothesisSet.hypotheses.map((hypothesis) => (
              <div className="brief-item" key={hypothesis.hypothesis_id}>
                <strong>{hypothesis.root_cause_description}</strong>
                <span>{Math.round(hypothesis.confidence * 100)}% confidence · {hypothesis.evidence.length} evidence records</span>
              </div>
            ))
          )}
          {!report?.evidence_brief && !hypothesisSet && <Empty title="No evidence yet" detail="Evidence appears only after hypotheses or a bridge report arrives." />}
        </div>
      </section>
      <section className="card">
        <div className="card-head">
          <div>
            <h2>{live ? "Connector targets" : "Action previews"}</h2>
            <p>Completion is shown only from connector SSE or a bridge report</p>
          </div>
          <Badge tone={live ? "bad" : "warn"}>{live ? "LIVE TARGETS" : "DRY RUN"}</Badge>
        </div>
        <div className="preview-list">
          {previews.map((preview, index) => (
            <div className="preview-row" key={`${preview.provider}-${index}`}>
              <span className="integration-icon small"><PreviewIcon provider={preview.provider} /></span>
              <div><strong>{preview.title ?? `${preview.provider} preview`}</strong><span>{previewDetail(preview)}</span></div>
              <Badge tone={live ? "bad" : "warn"}>{live ? "NOT CONFIRMED" : "NOT SENT"}</Badge>
            </div>
          ))}
          {previews.length === 0 && <Empty title="No action previews" detail="Linear, GitHub, and Slack previews appear only when supplied by GET /bridge/report/{alert_id}." />}
        </div>
      </section>
    </div>
  );
}

function connectorActionFingerprint(action: ConnectorAction): string {
  return `${action.idempotency_key}-${action.connector}-${action.operation}-${action.status}`;
}

function connectorActionKey(action: ConnectorAction, index: number): string {
  return `${connectorActionFingerprint(action)}-${index}`;
}

function connectorStatusTone(status: ConnectorActionStatus): "neutral" | "good" | "warn" | "bad" | "purple" {
  if (status === "succeeded") return "good";
  if (status === "failed") return "bad";
  if (status === "pending" || status === "running") return "warn";
  if (status === "replayed") return "purple";
  return "neutral";
}

function connectorActionLabel(action: ConnectorAction): string {
  const operation = action.operation.toLowerCase();
  if (action.status === "failed") return `${action.connector} operation failed`;
  if (action.status === "skipped") return `${action.connector} operation skipped`;
  if (action.status === "replayed") return `${action.connector} operation replayed`;
  const complete = action.status === "succeeded";
  if (action.connector === "linear") return complete ? "Linear issue created" : "Creating Linear issue";
  if (action.connector === "slack") return complete ? "Slack message posted" : "Posting Slack message";
  if (operation.includes("branch") || operation.includes("push")) {
    return complete ? "GitHub branch pushed" : "Pushing GitHub branch";
  }
  if (operation.includes("pr") || operation.includes("pull")) {
    return complete ? "GitHub pull request opened" : "Opening GitHub pull request";
  }
  return complete ? "GitHub operation completed" : "Running GitHub operation";
}

function ConnectorActionRow({ action, index }: { action: ConnectorAction; index: number }) {
  const url = safeExternalUrl(action.url);
  const reference = action.identifier ?? action.branch;
  return (
    <div className={`connector-action-row status-${action.status}`}>
      <span className="integration-icon small"><PreviewIcon provider={action.connector} /></span>
      <div className="connector-action-copy">
        <strong>{connectorActionLabel(action)}</strong>
        <span>
          {reference ? privateText(reference) : privateText(action.operation)}
          {action.connector === "slack" && action.posted_at ? ` · ${compactTime(action.posted_at)}` : ""}
        </span>
        {action.status === "failed" && action.error ? (
          <small>{privateText(action.error)}</small>
        ) : null}
        <code title={privateText(action.idempotency_key ?? "Idempotency key not reported")}>
          {privateText(action.idempotency_key ?? "Idempotency key not reported")}
        </code>
      </div>
      <div className="connector-action-state">
        <Badge tone={connectorStatusTone(action.status)}>{action.status}</Badge>
        {action.dry_run === true ? (
          <Badge tone="warn">DRY RUN</Badge>
        ) : action.dry_run === false ? (
          <Badge tone="bad">LIVE</Badge>
        ) : (
          <Badge>MODE UNKNOWN</Badge>
        )}
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${action.connector} result ${index + 1}`}>
            Open ↗
          </a>
        ) : null}
      </div>
    </div>
  );
}

function ConnectorActivity() {
  const { connectorActions, selectedAlertId, reports } = useRunbook();
  const report = selectedAlertId ? reports[selectedAlertId] : undefined;
  const reportActions = Array.isArray(report?.connector_writes?.actions)
    ? report.connector_writes.actions
    : [];
  const seen = new Set(connectorActions.map(connectorActionFingerprint));
  const actions = [
    ...connectorActions,
    ...reportActions.filter((action) => {
      const key = connectorActionFingerprint(action);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
  ];
  const connectorWrites = report?.connector_writes;
  const rollup = connectorWrites?.rollup ?? connectorWrites;
  const rollupStatus = String(rollup?.status ?? "").toLowerCase();
  const partial = rollup?.partial === true || rollupStatus.includes("partial") ||
    (Number(rollup?.succeeded ?? 0) > 0 && Number(rollup?.failed ?? 0) > 0);

  return (
    <section className="card connector-activity">
      <div className="card-head">
        <div>
          <h2>Connector write activity</h2>
          <p>Ordered events from connector_action SSE and authoritative bridge reports</p>
        </div>
        <div className="head-actions">
          {partial ? <Badge tone="bad">PARTIAL</Badge> : null}
          {rollupStatus ? <Badge tone={rollupStatus.includes("fail") ? "bad" : "neutral"}>{privateText(rollupStatus)}</Badge> : null}
          <Badge>{actions.length} EVENTS</Badge>
        </div>
      </div>
      <div className="connector-action-list" aria-live="polite">
        {actions.map((action, index) => (
          <ConnectorActionRow key={connectorActionKey(action, index)} action={action} index={index} />
        ))}
        {actions.length === 0 ? (
          <Empty
            title="No connector activity observed"
            detail="A decision response alone never marks a write successful. Waiting for connector_action SSE or connector_writes report data."
          />
        ) : null}
      </div>
    </section>
  );
}

function Dashboard() {
  const { alerts, hypotheses, proposals, resolutions, health } = useRunbook();
  const live = liveWritesEnabled(health);
  return (
    <div className="page">
      <ErrorBanner />
      <div className="hero-row">
        <div><span className="eyebrow">TESTTEAM · INCIDENT OPERATIONS</span><h2>Turn support signals into reviewed runbook decisions.</h2><p>{live ? "Live connector writes require an explicit per-alert confirmation at the human gate." : "Connector actions remain simulated while the bridge reports dry-run mode."}</p></div>
        <div className="stat-strip">
          <span><strong>{alerts.length}</strong><small>Alerts</small></span>
          <span><strong>{hypotheses.length}</strong><small>Diagnoses</small></span>
          <span><strong>{proposals.length}</strong><small>Awaiting review</small></span>
          <span><strong>{resolutions.length}</strong><small>Memories</small></span>
        </div>
      </div>
      <SlackSourceCard />
      <ManualComplaint />
      <AgentPipeline />
      <EvidenceAndPreviews />
      <ConnectorActivity />
    </div>
  );
}

function AlertListItem({
  alert,
  active,
  hypothesis,
  onClick
}: {
  alert: Alert;
  active: boolean;
  hypothesis?: HypothesisSet;
  onClick: () => void;
}) {
  return (
    <button className={`incident-list-item ${active ? "selected" : ""}`} onClick={onClick}>
      <span className={`severity ${alert.severity}`} />
      <span><strong>{alert.service}</strong><small>{alert.metric} · {alert.value} / {alert.threshold}</small></span>
      <span className="incident-meta"><Badge tone={alert.severity === "critical" ? "bad" : "warn"}>{alert.severity}</Badge><small>{hypothesis ? `${hypothesis.hypotheses.length} hypotheses` : "awaiting evidence"}</small></span>
    </button>
  );
}

function Inbox() {
  const { alerts, hypotheses, selectedAlertId, chooseAlert, reports } = useRunbook();
  const selected = alerts.find((item) => item.alert_id === selectedAlertId) ?? alerts[0];
  const selectedHypotheses = hypotheses.find((item) => item.alert_id === selected?.alert_id);
  const report = selected ? reports[selected.alert_id] : undefined;
  if (alerts.length === 0) return <div className="page"><ErrorBanner /><Empty title="No incident evidence" detail="Inbox remains empty until the backend emits an alert event." /></div>;
  return (
    <div className="split-view">
      <div className="incident-list">
        <div className="split-head"><span>INCIDENT EVIDENCE</span><small>{alerts.length} observed</small></div>
        {alerts.map((alert) => <AlertListItem key={alert.alert_id} alert={alert} active={alert.alert_id === selected?.alert_id} hypothesis={hypotheses.find((item) => item.alert_id === alert.alert_id)} onClick={() => void chooseAlert(alert.alert_id)} />)}
      </div>
      <div className="incident-detail">
        <ErrorBanner />
        {selected && (
          <>
            <div className="detail-head">
              <div><span className="eyebrow">{selected.alert_id}</span><h2>{selected.service}: {selected.metric}</h2><p>Observed {selected.value}; threshold {selected.threshold} · fired {compactTime(selected.fired_at)}</p></div>
              <Badge tone={selected.severity === "critical" ? "bad" : "warn"}>{selected.severity.toUpperCase()}</Badge>
            </div>
            <section className="card">
              <div className="card-head"><div><h2>Grounded hypotheses</h2><p>Evidence is displayed as returned; no fallback diagnosis is invented.</p></div></div>
              <div className="hypothesis-list">
                {!selectedHypotheses && <Empty title="Diagnosis pending" detail="No hypotheses event has arrived for this alert." />}
                {selectedHypotheses?.hypotheses.map((hypothesis) => (
                  <div className="hypothesis" key={hypothesis.hypothesis_id}>
                    <div className="hypothesis-head"><strong>{hypothesis.root_cause_description}</strong><Badge tone="purple">{Math.round(hypothesis.confidence * 100)}%</Badge></div>
                    <p>{hypothesis.reasoning}</p>
                    <div className="evidence-list">
                      {hypothesis.evidence.map((evidence) => <div key={`${evidence.source}-${evidence.ref}`}><Badge>{evidence.source}</Badge><span><strong>{evidence.ref}</strong>{evidence.detail}</span></div>)}
                    </div>
                  </div>
                ))}
              </div>
            </section>
            <section className="card report-card">
              <div className="card-head"><div><h2>Bridge evidence brief</h2><p>GET /bridge/report/{selected.alert_id}</p></div></div>
              <div className="brief-body">{renderBrief(report) ?? <Empty title="No bridge report yet" detail="The report endpoint returned no cached brief or has not completed." />}</div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

function connectorTargets(
  proposal: ActionProposal,
  report: BridgeReport | undefined,
  health: BridgeHealth | null
): { connector: "linear" | "github" | "slack"; target: string }[] {
  const previews = collectPreviews(report);
  const previewTarget = (connector: string) =>
    previews.find((preview) => preview.provider.toLowerCase().includes(connector))?.target;
  return [
    {
      connector: "linear",
      target: previewTarget("linear") ?? "Configured Linear workspace"
    },
    {
      connector: "github",
      target:
        health?.github_repo_allowed ??
        previewTarget("github") ??
        "No allowed GitHub repository reported"
    },
    {
      connector: "slack",
      target: previewTarget("slack") ?? `Configured Slack destination for ${proposal.alert_id}`
    }
  ];
}

function ApprovalCard({ proposal }: { proposal: ActionProposal }) {
  const { decideProposal, decisionBusy, health, reports } = useRunbook();
  const [note, setNote] = useState("");
  const [destructiveConfirmation, setDestructiveConfirmation] = useState("");
  const [liveConfirmation, setLiveConfirmation] = useState("");
  const [liveIntent, setLiveIntent] = useState(false);
  const destructive = proposal.safety_class === "destructive";
  const live = liveWritesEnabled(health);
  const busy = decisionBusy === proposal.proposal_id;
  const targets = connectorTargets(proposal, reports[proposal.alert_id], health);
  const approveDisabled =
    busy ||
    (destructive && destructiveConfirmation !== proposal.action_target) ||
    (live && (!liveIntent || liveConfirmation !== proposal.alert_id));
  const decide = (decision: "approve" | "reject") => {
    const approveLive = decision === "approve" && live;
    void decideProposal({
      proposal_id: proposal.proposal_id,
      decision,
      reviewer_note: note.trim() || undefined,
      typed_confirmation:
        decision === "approve" && destructive ? destructiveConfirmation : undefined,
      execute_live_writes: approveLive ? true : undefined,
      live_writes_confirmation: approveLive ? liveConfirmation : undefined
    }).catch(() => undefined);
  };
  return (
    <section className="card approval-card">
      <div className="approval-accent" />
      <div className="approval-main">
        <div className="approval-title">
          <div><span className="eyebrow">{proposal.proposal_id} · {proposal.alert_id}</span><h2>{proposal.action_type.replaceAll("_", " ")} <span>on</span> {proposal.action_target}</h2></div>
          <Badge tone={destructive ? "bad" : proposal.safety_class === "standard" ? "warn" : "good"}>{proposal.safety_class}</Badge>
        </div>
        <p className="reasoning">{proposal.reasoning}</p>
        <div className="proposal-meta">
          <span><small>Runbook source</small><strong>{proposal.runbook_source}</strong></span>
          <span><small>Remediator confidence</small><strong>{Math.round(proposal.remediator_confidence * 100)}%</strong></span>
          <span><small>Execution boundary</small><strong>{live ? "LIVE CONNECTOR WRITES" : "SIMULATED / DRY RUN"}</strong></span>
          <span><small>Guild task</small><strong>{proposal.guild_task_id ?? "Not provided"}</strong></span>
        </div>
        <label className="field-label">Reviewer note <span>optional</span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} placeholder="Document the reason for this decision…" /></label>
        {destructive && (
          <label className="field-label destructive-confirm">
            Type <code>{proposal.action_target}</code> to approve
            <input value={destructiveConfirmation} onChange={(event) => setDestructiveConfirmation(event.target.value)} autoComplete="off" />
            <small>Exact typed confirmation is required by the existing decision contract.</small>
          </label>
        )}
        {live ? (
          <div className="live-approval-confirm">
            <strong>Live connector targets</strong>
            <p>These systems may be changed after approval. Message bodies remain hidden here.</p>
            <div className="live-target-list">
              {targets.map((target) => (
                <div key={target.connector}>
                  <span className="integration-icon small"><PreviewIcon provider={target.connector} /></span>
                  <span><strong>{target.connector}</strong><small>{privateText(target.target)}</small></span>
                </div>
              ))}
            </div>
            <label className="intent-check">
              <input
                type="checkbox"
                checked={liveIntent}
                onChange={(event) => setLiveIntent(event.target.checked)}
              />
              <span>I intend to execute live writes against these connector targets.</span>
            </label>
            <label className="field-label">
              Type alert ID <code>{proposal.alert_id}</code> exactly
              <input
                value={liveConfirmation}
                onChange={(event) => setLiveConfirmation(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <small>Both intent and exact alert ID confirmation are required.</small>
            </label>
          </div>
        ) : null}
        <div className="approval-buttons">
          <button className="button ghost" disabled={busy} onClick={() => decide("reject")}>{busy ? "Submitting…" : "Reject"}</button>
          <button className={`button ${live ? "danger" : "primary"}`} disabled={approveDisabled} onClick={() => decide("approve")}>{busy ? "Submitting…" : live ? "Approve live writes" : "Approve dry run"}</button>
        </div>
      </div>
    </section>
  );
}

function Approvals() {
  const { proposals, decisions, health } = useRunbook();
  const live = liveWritesEnabled(health);
  return (
    <div className="page narrow">
      <ErrorBanner />
      <div className="section-intro"><div><span className="eyebrow">HUMAN-IN-THE-LOOP</span><h2>Pending approvals</h2><p>{live ? "Live approval requires explicit intent and the exact alert ID for every proposal." : "Approval retains the runtime's simulated connector path."}</p></div><Badge tone={live ? "bad" : "warn"}>{proposals.length} WAITING</Badge></div>
      {proposals.length === 0 ? <Empty title="Nothing waiting on you" detail={decisions.length ? `${decisions.length} decision(s) were accepted in this session.` : "Proposals appear only after the backend emits a proposal event."} /> : proposals.map((proposal) => <ApprovalCard key={proposal.proposal_id} proposal={proposal} />)}
    </div>
  );
}

function ResolutionCard({ resolution }: { resolution: Resolution }) {
  return (
    <section className="memory-card">
      <div className="memory-icon">F</div>
      <div className="memory-copy">
        <div><span className="eyebrow">FALKOR FINAL INCIDENT</span><h2>{resolution.incident_id}</h2></div>
        <div className="memory-grid">
          <span><small>Alert</small><strong>{resolution.alert_id}</strong></span>
          <span><small>Outcome</small><strong>{resolution.outcome}</strong></span>
          <span><small>Decision</small><strong>{resolution.reviewer_decision.decision}</strong></span>
          <span><small>Latency</small><strong>{resolution.total_latency_ms} ms</strong></span>
          <span><small>Recorded cost</small><strong>${resolution.cost_usd.toFixed(4)}</strong></span>
          <span><small>Final action</small><strong>{resolution.final_action?.action_type ?? "none"}</strong></span>
        </div>
      </div>
      <Badge tone={resolution.outcome === "verified" ? "good" : resolution.outcome === "rejected" ? "bad" : "warn"}>{resolution.outcome}</Badge>
    </section>
  );
}

function Memory() {
  const { resolutions } = useRunbook();
  return (
    <div className="page narrow">
      <ErrorBanner />
      <div className="section-intro"><div><span className="eyebrow">FALKORDB MEMORY</span><h2>Final incident records</h2><p>Only resolution events with real final incident IDs are listed here.</p></div><Badge>{resolutions.length} RECORDS</Badge></div>
      {resolutions.length === 0 ? <Empty title="No final incident IDs" detail="Memory remains empty until a resolution is persisted and emitted by the backend." /> : resolutions.map((resolution) => <ResolutionCard key={resolution.incident_id} resolution={resolution} />)}
    </div>
  );
}

function IntegrationStatus({
  icon,
  name,
  detail,
  status
}: {
  icon: ReactNode;
  name: string;
  detail: string;
  status: boolean | null;
}) {
  return (
    <div className="setting-integration">
      <span className="integration-icon">{icon}</span>
      <span><strong>{name}</strong><small>{detail}</small></span>
      <Badge tone={status === true ? "good" : status === false ? "bad" : "neutral"}>{status === true ? "CONFIGURED" : status === false ? "NOT CONFIGURED" : "UNKNOWN"}</Badge>
    </div>
  );
}

function guildStatus(health: BridgeHealth | null, state: string) {
  if (state === "loading") return { label: "PENDING", tone: "warn" as const, detail: "Waiting for bridge health." };
  if (!health) return { label: "UNKNOWN", tone: "neutral" as const, detail: "Health unavailable; no runtime claim." };
  const qualifying = health.guild_qualifying ?? health.sponsor_health?.guild_qualifying;
  const reportedMode = health.guild_mode ?? health.sponsor_health?.guild_mode;
  if (qualifying === true) return { label: "QUALIFYING", tone: "good" as const, detail: String(reportedMode ?? "Qualifying runtime reported.") };
  const mode = String(reportedMode ?? "not reported");
  return {
    label: mode.toLowerCase().includes("pending") ? "PENDING" : "NON-QUALIFYING",
    tone: "warn" as const,
    detail: `${mode}. Experiment tracking does not prove runtime handoff.`
  };
}

function Settings() {
  const { health, healthState, streamConnected } = useRunbook();
  const slack = healthFlag(health, "slack_connected");
  const linear = healthFlag(health, "linear_configured");
  const github = healthFlag(health, "github_configured");
  const guild = guildStatus(health, healthState);
  const live = liveWritesEnabled(health);
  const allowedRepo = health?.github_repo_allowed
    ? privateText(health.github_repo_allowed)
    : "Not reported";
  return (
    <div className="settings-page">
      <div className="settings-nav"><button className="active">Integrations</button><button>Runtime boundaries</button><button>Workspace</button></div>
      <div className="settings-content">
        <ErrorBanner />
        <div className={`settings-mode-banner ${live ? "live" : "dry"}`}>
          <Badge tone={live ? "bad" : "warn"}>{live ? "LIVE WRITES ENABLED" : "DRY RUN"}</Badge>
          <div>
            <strong>{live ? "Controlled external writes are available" : "External actions are previews only"}</strong>
            <span>{live ? "Every live approval requires explicit intent and exact alert ID confirmation." : "Connector output must not be read as a production write."}</span>
          </div>
        </div>
        <section>
          <div className="section-title"><h2>Connector write boundary</h2><p>Mode and repository allowance come from GET /bridge/health. Credentials are never displayed.</p></div>
          <div className="workspace-settings connector-boundary-settings">
            <span><small>Connector mode</small><strong className={live ? "live-text" : ""}>{live ? "LIVE WRITES ENABLED" : "DRY RUN"}</strong></span>
            <span><small>Allowed GitHub repository</small><strong className="mono">{allowedRepo}</strong></span>
          </div>
        </section>
        <section>
          <div className="section-title"><h2>Operational integrations</h2><p>Status comes from GET /bridge/health; unknown values stay unknown.</p></div>
          <div className="settings-list">
            <IntegrationStatus icon={<SlackBrand />} name="Slack" detail="Incident intake via bridge messages" status={slack} />
            <IntegrationStatus icon={<LinearBrand />} name="Linear" detail={live ? "Controlled issue write destination" : "Dry-run issue preview destination"} status={linear} />
            <IntegrationStatus icon={<GitHubBrand />} name="GitHub" detail={live ? `Allowed repository: ${allowedRepo}` : "Dry-run change preview destination"} status={github} />
          </div>
        </section>
        <section>
          <div className="section-title"><h2>Sponsor boundaries</h2><p>Capabilities are named narrowly to avoid overstating runtime behavior.</p></div>
          <div className="boundary-grid">
            <div><span className="boundary-mark laser">L</span><strong>LaserData</strong><p>Event transport for alerts, hypotheses, proposals, decisions, and resolutions.</p><Badge tone={streamConnected ? "good" : "bad"}>{streamConnected ? "SSE OBSERVED" : "SSE NOT OBSERVED"}</Badge></div>
            <div><span className="boundary-mark falkor">F</span><strong>FalkorDB</strong><p>Final incident memory. IDs are displayed only after a real resolution event.</p><Badge>FINAL MEMORY</Badge></div>
            <div><span className="boundary-mark rocket">R</span><strong>RocketRide</strong><p>Inference runtime boundary for diagnosis and remediation; no browser credential access.</p><Badge>BACKEND-ONLY</Badge></div>
            <div><span className="boundary-mark guild">G</span><strong>Guild</strong><p>{guild.detail}</p><Badge tone={guild.tone}>{guild.label}</Badge></div>
          </div>
        </section>
        <section>
          <div className="section-title"><h2>Workspace</h2><p>Fake company chrome for this demo; not backend account data.</p></div>
          <div className="workspace-settings"><span><small>Company shell</small><strong>TestTeam · DEMO CHROME</strong></span><span><small>Product</small><strong>Runbook Incident Command</strong></span><span><small>Serving base</small><strong className="mono">/demo/</strong></span><span><small>Bridge health</small><strong>{healthState.toUpperCase()}</strong></span></div>
        </section>
      </div>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");
  const content = useMemo(() => {
    if (activeTab === "dashboard") return <Dashboard />;
    if (activeTab === "inbox") return <Inbox />;
    if (activeTab === "approvals") return <Approvals />;
    if (activeTab === "memory") return <Memory />;
    return <Settings />;
  }, [activeTab]);
  return (
    <div className="app-shell">
      <Sidebar active={activeTab} setActive={setActiveTab} />
      <div className="main-shell">
        <TopBar active={activeTab} />
        <WriteModeBanner />
        <main>{content}</main>
      </div>
    </div>
  );
}
