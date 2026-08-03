/* oxlint-disable react/only-export-components -- provider and its hook intentionally share one private context */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode
} from "react";
import {
  getBridgeHealth,
  getBridgeReport,
  getSlackMessages,
  postComplaint,
  postDecision,
  subscribeEvents,
  type ComplaintRequest,
  type DecisionRequest
} from "./api";
import type {
  ActionEvent,
  ActionProposal,
  Alert,
  BridgeHealth,
  BridgeReport,
  ConnectorAction,
  HypothesisSet,
  LoadState,
  OutcomeEvent,
  Resolution,
  ReviewerDecision,
  RunbookEventName,
  RunbookEventPayload,
  SlackMessage,
  TimelineEvent
} from "./contracts";

interface RunbookState {
  health: BridgeHealth | null;
  healthState: LoadState;
  messages: SlackMessage[];
  messagesState: LoadState;
  messagesSyncing: boolean;
  messagesLastSyncedAt: string | null;
  messagesError: string | null;
  streamConnected: boolean;
  selectedMessageId: string | null;
  selectedAlertId: string | null;
  alerts: Alert[];
  hypotheses: HypothesisSet[];
  proposals: ActionProposal[];
  decisions: ReviewerDecision[];
  actions: ActionEvent[];
  connectorActions: ConnectorAction[];
  outcomes: OutcomeEvent[];
  resolutions: Resolution[];
  reports: Record<string, BridgeReport>;
  timeline: TimelineEvent[];
  running: boolean;
  error: string | null;
  decisionBusy: string | null;
}

interface RunbookStore extends RunbookState {
  refreshMessages: () => Promise<void>;
  runMessage: (message: SlackMessage) => Promise<void>;
  runManualComplaint: (text: string) => Promise<void>;
  chooseAlert: (alertId: string) => Promise<void>;
  decideProposal: (request: DecisionRequest) => Promise<void>;
}

const initialState: RunbookState = {
  health: null,
  healthState: "loading",
  messages: [],
  messagesState: "loading",
  messagesSyncing: false,
  messagesLastSyncedAt: null,
  messagesError: null,
  streamConnected: false,
  selectedMessageId: null,
  selectedAlertId: null,
  alerts: [],
  hypotheses: [],
  proposals: [],
  decisions: [],
  actions: [],
  connectorActions: [],
  outcomes: [],
  resolutions: [],
  reports: {},
  timeline: [],
  running: false,
  error: null,
  decisionBusy: null
};

const RunbookContext = createContext<RunbookStore | null>(null);

function messageError(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected backend error";
}

function eventId(name: string, payload: RunbookEventPayload): string {
  const record = payload as unknown as Record<string, unknown>;
  return `${name}-${String(record.idempotency_key ?? record.proposal_id ?? record.alert_id ?? record.incident_id ?? Date.now())}-${Date.now()}`;
}

function eventTime(payload: RunbookEventPayload): string {
  const record = payload as unknown as Record<string, unknown>;
  return String(
    record.posted_at ??
      record.timestamp ??
      record.generated_at ??
      record.fired_at ??
      new Date().toISOString()
  );
}

function upsert<T>(items: T[], next: T, key: (item: T) => string): T[] {
  const id = key(next);
  return [next, ...items.filter((item) => key(item) !== id)];
}

export function RunbookProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RunbookState>(initialState);
  const messagesRequestRef = useRef<AbortController | null>(null);

  const syncMessages = useCallback(async (showLoading: boolean) => {
    if (messagesRequestRef.current) return;
    const controller = new AbortController();
    messagesRequestRef.current = controller;
    setState((current) => ({
      ...current,
      messagesState: showLoading && current.messages.length === 0 ? "loading" : current.messagesState,
      messagesSyncing: true,
      messagesError: null,
      error: current.error?.startsWith("Slack messages:") ? null : current.error
    }));
    try {
      const messages = await getSlackMessages(controller.signal);
      setState((current) => ({
        ...current,
        messages,
        messagesState: "ready",
        messagesSyncing: false,
        messagesLastSyncedAt: new Date().toISOString(),
        messagesError: null,
        error: current.error?.startsWith("Slack messages:") ? null : current.error
      }));
    } catch (error) {
      if (controller.signal.aborted) return;
      const detail = `Slack messages: ${messageError(error)}`;
      setState((current) => ({
        ...current,
        messagesState: current.messages.length === 0 ? "error" : "ready",
        messagesSyncing: false,
        messagesError: detail,
        error: detail
      }));
    } finally {
      if (messagesRequestRef.current === controller) messagesRequestRef.current = null;
    }
  }, []);

  const refreshMessages = useCallback(() => syncMessages(true), [syncMessages]);

  useEffect(() => {
    const controller = new AbortController();
    void getBridgeHealth(controller.signal).then(
      (health) => setState((current) => ({ ...current, health, healthState: "ready" })),
      (error) => {
        if (controller.signal.aborted) return;
        setState((current) => ({
          ...current,
          health: null,
          healthState: "error",
          error: `Bridge health: ${messageError(error)}`
        }));
      }
    );
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const poll = () => {
      if (document.visibilityState === "visible") void syncMessages(false);
    };
    poll();
    const intervalId = window.setInterval(poll, 4_500);
    document.addEventListener("visibilitychange", poll);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", poll);
      const activeRequest = messagesRequestRef.current;
      messagesRequestRef.current = null;
      activeRequest?.abort();
    };
  }, [syncMessages]);

  const handleEvent = useCallback((name: RunbookEventName, payload: RunbookEventPayload) => {
    setState((current) => {
      const next: RunbookState = {
        ...current,
        timeline: [
          { id: eventId(name, payload), type: name, at: eventTime(payload), payload },
          ...current.timeline
        ].slice(0, 100),
        error: null
      };
      switch (name) {
        case "alert": {
          const alert = payload as Alert;
          next.alerts = upsert(current.alerts, alert, (item) => item.alert_id);
          next.selectedAlertId = alert.alert_id;
          next.running = true;
          break;
        }
        case "hypotheses": {
          const hypotheses = payload as HypothesisSet;
          next.hypotheses = upsert(current.hypotheses, hypotheses, (item) => item.alert_id);
          next.selectedAlertId = hypotheses.alert_id;
          break;
        }
        case "proposal": {
          const proposal = payload as ActionProposal;
          next.proposals = upsert(current.proposals, proposal, (item) => item.proposal_id);
          next.selectedAlertId = proposal.alert_id;
          next.running = false;
          break;
        }
        case "decision": {
          const decision = payload as ReviewerDecision;
          const decidedProposal = current.proposals.find(
            (proposal) => proposal.proposal_id === decision.proposal_id
          );
          next.decisions = upsert(current.decisions, decision, (item) => item.proposal_id);
          next.proposals = current.proposals.filter(
            (proposal) => proposal.proposal_id !== decision.proposal_id
          );
          if (decidedProposal) next.selectedAlertId = decidedProposal.alert_id;
          next.decisionBusy =
            current.decisionBusy === decision.proposal_id ? null : current.decisionBusy;
          next.running = decision.decision !== "reject";
          break;
        }
        case "action": {
          const action = payload as ActionEvent;
          next.actions = upsert(current.actions, action, (item) =>
            `${item.proposal_id}-${item.status}`
          );
          next.selectedAlertId = action.alert_id;
          next.running = true;
          break;
        }
        case "outcome": {
          const outcome = payload as OutcomeEvent;
          next.outcomes = upsert(current.outcomes, outcome, (item) =>
            `${item.proposal_id}-${item.status}`
          );
          next.selectedAlertId = outcome.alert_id;
          break;
        }
        case "resolution": {
          const resolution = payload as Resolution;
          next.resolutions = upsert(current.resolutions, resolution, (item) => item.incident_id);
          next.selectedAlertId = resolution.alert_id;
          next.running = false;
          break;
        }
        case "bridge_report": {
          const report = payload as BridgeReport;
          if (report.alert_id) {
            next.reports = { ...current.reports, [report.alert_id]: report };
            next.selectedAlertId = report.alert_id;
          }
          break;
        }
        case "connector_action": {
          const connectorAction = payload as ConnectorAction;
          next.connectorActions = [
            ...current.connectorActions,
            connectorAction
          ].slice(-100);
          if (connectorAction.alert_id) next.selectedAlertId = connectorAction.alert_id;
          break;
        }
      }
      return next;
    });

    if (name === "resolution") {
      const alertId = (payload as Resolution).alert_id;
      void getBridgeReport(alertId)
        .then((report) =>
          setState((current) => ({
            ...current,
            reports: { ...current.reports, [alertId]: report }
          }))
        )
        .catch(() => {
          // A streamed resolution remains valid even if its optional bridge report is unavailable.
        });
    }
  }, []);

  useEffect(
    () =>
      subscribeEvents(
        handleEvent,
        (connected) => setState((current) => ({ ...current, streamConnected: connected }))
      ),
    [handleEvent]
  );

  const submitComplaint = useCallback(async (payload: ComplaintRequest, messageId?: string) => {
    setState((current) => ({
      ...current,
      running: true,
      selectedMessageId: messageId ?? null,
      error: null
    }));
    try {
      const accepted = await postComplaint(payload);
      setState((current) => ({
        ...current,
        selectedAlertId: accepted.alert_id ?? current.selectedAlertId
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        running: false,
        error: `Complaint submission: ${messageError(error)}`
      }));
      throw error;
    }
  }, []);

  const runMessage = useCallback(
    async (message: SlackMessage) => {
      await submitComplaint(
        {
          text: message.text,
          external_id: message.ts ?? message.id,
          channel: message.channel,
        },
        message.id
      );
    },
    [submitComplaint]
  );

  const runManualComplaint = useCallback(
    async (text: string) => {
      await submitComplaint({ text });
    },
    [submitComplaint]
  );

  const chooseAlert = useCallback(async (alertId: string) => {
    setState((current) => ({ ...current, selectedAlertId: alertId }));
    try {
      const report = await getBridgeReport(alertId);
      setState((current) => ({
        ...current,
        reports: { ...current.reports, [alertId]: report }
      }));
    } catch {
      // Evidence from SSE remains visible; report absence is represented in the UI.
    }
  }, []);

  const decideProposal = useCallback(async (request: DecisionRequest) => {
    setState((current) => ({ ...current, decisionBusy: request.proposal_id, error: null }));
    try {
      await postDecision(request);
    } catch (error) {
      setState((current) => ({
        ...current,
        decisionBusy: null,
        error: `Decision submission: ${messageError(error)}`
      }));
      throw error;
    }
  }, []);

  const value = useMemo<RunbookStore>(
    () => ({
      ...state,
      refreshMessages,
      runMessage,
      runManualComplaint,
      chooseAlert,
      decideProposal
    }),
    [state, refreshMessages, runMessage, runManualComplaint, chooseAlert, decideProposal]
  );

  return <RunbookContext.Provider value={value}>{children}</RunbookContext.Provider>;
}

export function useRunbook() {
  const context = useContext(RunbookContext);
  if (!context) throw new Error("useRunbook must be used inside RunbookProvider");
  return context;
}
