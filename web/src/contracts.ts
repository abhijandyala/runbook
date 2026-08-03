export type Tab = "dashboard" | "inbox" | "approvals" | "memory" | "settings";
export type LoadState = "idle" | "loading" | "ready" | "error";
export type SafetyClass = "safe" | "standard" | "destructive";
export type DecisionValue = "approve" | "reject" | "modify";

export interface BridgeHealth {
  status?: string;
  ok?: boolean;
  dry_run?: boolean;
  connector_dry_run?: boolean;
  connector_live_writes_enabled?: boolean;
  github_repo_allowed?: string | null;
  slack_connected?: boolean;
  slack_configured?: boolean;
  linear_configured?: boolean;
  github_configured?: boolean;
  guild_mode?: string;
  guild_qualifying?: boolean;
  sponsor_health?: {
    status?: string;
    guild_mode?: string;
    guild_qualifying?: boolean;
    [key: string]: unknown;
  };
  integrations?: Record<string, boolean | string | null>;
  [key: string]: unknown;
}

export interface SlackMessage {
  id: string;
  text: string;
  user?: string;
  user_name?: string;
  channel?: string;
  name?: string;
  channel_name?: string;
  timestamp?: string;
  ts?: string;
  permalink?: string;
  [key: string]: unknown;
}

export interface Alert {
  alert_id: string;
  fired_at: string;
  severity: "critical" | "warning" | "info";
  service: string;
  metric: string;
  value: number;
  threshold: number;
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
}

export interface Evidence {
  source: "graph" | "linkup" | "past_incident" | "alert";
  ref: string;
  detail: string;
}

export interface Hypothesis {
  hypothesis_id: string;
  type: string;
  root_cause_description: string;
  affected_entity: string;
  confidence: number;
  evidence: Evidence[];
  reasoning: string;
}

export interface HypothesisSet {
  alert_id: string;
  hypotheses: Hypothesis[];
  linkup_hits: number;
  graph_query_ms: number;
  generated_at: string;
  guild_handoff_id?: string | null;
}

export interface ActionProposal {
  proposal_id: string;
  alert_id: string;
  target_hypothesis_id: string;
  action_type: string;
  action_target: string;
  action_params: Record<string, unknown>;
  safety_class: SafetyClass;
  remediator_confidence: number;
  runbook_source: string;
  reasoning: string;
  guild_task_id?: string | null;
}

export interface ReviewerDecision {
  proposal_id: string;
  decision: DecisionValue;
  modified_action?: ActionProposal | null;
  reviewer_note?: string | null;
  timestamp: string;
  guild_task_id?: string | null;
}

export interface ActionEvent {
  proposal_id: string;
  alert_id: string;
  action_type: string;
  action_target: string;
  status: string;
  simulated: boolean;
  timestamp: string;
  message?: string;
}

export interface OutcomeEvent {
  proposal_id: string;
  alert_id: string;
  status: string;
  outcome?: string;
  observed_value?: number | null;
  threshold?: number;
  simulated: boolean;
  timestamp: string;
}

export interface Resolution {
  incident_id: string;
  alert_id: string;
  final_action?: ActionProposal | null;
  outcome: "verified" | "partial" | "no_effect" | "rejected";
  reviewer_decision: ReviewerDecision;
  total_latency_ms: number;
  cost_usd: number;
}

export interface ActionPreview {
  provider: "linear" | "github" | "slack" | string;
  title?: string;
  body?: string;
  target?: string;
  status?: string;
  dry_run?: boolean;
  [key: string]: unknown;
}

export type ConnectorName = "linear" | "github" | "slack";
export type ConnectorActionStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped"
  | "replayed";

export interface ConnectorAction {
  connector: ConnectorName;
  operation: string;
  status: ConnectorActionStatus;
  dry_run: boolean;
  idempotency_key: string;
  url?: string | null;
  identifier?: string | null;
  branch?: string | null;
  posted_at?: string | null;
  error?: string | null;
  alert_id?: string;
  proposal_id?: string;
}

export interface ConnectorWritesRollup {
  status?: string;
  total?: number;
  pending?: number;
  running?: number;
  succeeded?: number;
  failed?: number;
  skipped?: number;
  replayed?: number;
  partial?: boolean;
  dry_run?: boolean;
  [key: string]: unknown;
}

export interface ConnectorWritesReport extends ConnectorWritesRollup {
  rollup?: ConnectorWritesRollup;
  actions?: ConnectorAction[];
  proposal_id?: string;
  alert_id?: string;
  started_at?: string;
  completed_at?: string;
}

export interface BridgeReport {
  alert_id?: string;
  incident_id?: string;
  evidence_brief?: string | string[] | Record<string, unknown>;
  action_previews?: ActionPreview[] | Record<string, unknown>;
  linear_preview?: Record<string, unknown>;
  github_preview?: Record<string, unknown>;
  slack_preview?: Record<string, unknown>;
  connector_writes?: ConnectorWritesReport;
  [key: string]: unknown;
}

export type RunbookEventName =
  | "alert"
  | "hypotheses"
  | "proposal"
  | "decision"
  | "action"
  | "outcome"
  | "resolution"
  | "bridge_report"
  | "connector_action";

export type RunbookEventPayload =
  | Alert
  | HypothesisSet
  | ActionProposal
  | ReviewerDecision
  | ActionEvent
  | OutcomeEvent
  | Resolution
  | BridgeReport
  | ConnectorAction;

export interface TimelineEvent {
  id: string;
  type: RunbookEventName;
  at: string;
  payload: RunbookEventPayload;
}
