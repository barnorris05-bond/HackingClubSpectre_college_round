// API adapter for the FastAPI routes and ControlState snapshot used by this app.

export type SessionLifecycle = 'idle' | 'active' | 'stopped';
export type ServiceStatus = 'available' | 'unavailable' | 'initializing' | 'stopped' | 'error';
export type TrackingStatus = 'unavailable' | 'lost' | 'tracking';

export interface SessionState {
  session_id: string;
  lifecycle: SessionLifecycle;
  paused: boolean;
  emergency_stopped: boolean;
  voice_status: ServiceStatus;
  vision_status: ServiceStatus;
  browser_status: ServiceStatus;
  face_detected: boolean;
  tracking_status: TrackingStatus;
  tracking_confidence: number | null;
  target_x: number | null;
  target_y: number | null;
  target_stable: boolean;
  calibrated: boolean;
  cursor_control_enabled: boolean;
  browser_permission_enabled: boolean;
  current_command_id: string | null;
  last_command_result: CommandResult | null;
  active_warnings: string[];
  started_at: string;
  updated_at: string;
}

export interface SessionActionResponse { state: SessionState; }
export type CommandKind = 'direct_click' | 'browser' | 'dictation' | 'unknown';
export type CommandStatus = 'received' | 'processing' | 'completed' | 'blocked' | 'failed';
export interface CommandResult {
  command_id: string | null;
  kind: CommandKind;
  status: CommandStatus;
  message: string;
  reason_code?: string | null;
  details: Record<string, unknown>;
}

export interface HealthResponse { status: string; timestamp: string; }
export interface ReadinessResponse {
  ready: boolean;
  safe: boolean;
  partial: boolean;
  services: { voice: ServiceStatus; vision: ServiceStatus; browser: ServiceStatus };
}

export interface BackendStatus {
  paused?: boolean;
  emergency_stop?: boolean;
  cursor_control_enabled?: boolean;
  camera?: { status?: string; available?: boolean; detail?: string | null };
  tracking?: { active?: boolean; reason?: string; quality?: number; faces?: number };
  calibration?: { calibrated?: boolean; state?: string };
  cursor?: { nx?: number; ny?: number; valid?: boolean };
  clicks?: unknown;
  voice?: {
    mode?: string;
    last_command?: string | null;
    last_command_kind?: string | null;
    last_command_at?: string | null;
    last_command_executed?: boolean | null;
    last_command_result?: Record<string, unknown> | null;
  };
  browser?: { status?: string; detail?: string | null };
  browser_status?: { available?: boolean; started?: boolean; errors?: unknown[]; detail?: string | null };
  server_time?: string;
  last_updated?: string;
  vision?: { available?: boolean; running?: boolean; unavailable_reason?: string | null };
  dictation?: unknown;
}

const asRecord = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
const text = (value: unknown): string => typeof value === 'string' ? value : '';
const serviceStatus = (value: unknown): ServiceStatus => {
  if (value === 'available' || value === 'unavailable' || value === 'initializing' || value === 'stopped' || value === 'error') return value;
  return 'unavailable';
};

function mapVisionStatus(raw: BackendStatus): ServiceStatus {
  const cameraState = text(raw.camera?.status);
  if (raw.vision?.available === false || raw.camera?.available === false || cameraState === 'unavailable') return 'unavailable';
  if (cameraState === 'starting') return 'initializing';
  if (cameraState === 'running') return 'available';
  if (cameraState === 'error') return 'error';
  if (cameraState === 'stopped' || cameraState === 'not_initialized') return 'stopped';
  return 'unavailable';
}

function mapBrowserStatus(raw: BackendStatus): ServiceStatus {
  if (raw.browser_status?.started === true || raw.browser?.status === 'running') return 'available';
  if (raw.browser_status?.errors?.length || raw.browser?.status === 'error') return 'error';
  if (raw.browser_status?.available === false || raw.browser?.status === 'unavailable') return 'unavailable';
  if (raw.browser?.status === 'stopped' || raw.browser?.status === 'not_started') return 'stopped';
  return 'unavailable';
}

const SAFETY_REASONS = new Set(['paused', 'emergency_stop', 'cursor_disabled', 'injection_disabled', 'not_stable', 'low_quality', 'cooldown']);

/** Convert Runtime.handle_voice's actual result into the UI command contract. */
export function mapCommandResult(value: unknown): CommandResult {
  const raw = asRecord(value);
  const action = asRecord(raw.action_result);
  const decision = asRecord(raw.decision);
  const reason = text(decision.reason) || text(action.reason) || text(raw.reason) || undefined;
  const executed = raw.executed === true || decision.allowed === true || action.ok === true;
  const explicitlyRejected = raw.executed === false || decision.allowed === false || action.ok === false;
  const status: CommandStatus = executed
    ? 'completed'
    : decision.allowed === false || explicitlyRejected && reason && SAFETY_REASONS.has(reason) ? 'blocked'
      : explicitlyRejected ? 'failed' : 'failed';
  const transcript = text(raw.transcript);
  const detail = text(action.detail) || (typeof raw.action_result === 'string' ? raw.action_result : '');
  const message = status === 'completed'
    ? detail || (transcript ? `Executed: ${transcript}` : 'Command completed')
    : text(decision.detail) || detail || text(action.reason) || reason || (transcript ? `Command did not execute: ${transcript}` : 'Command result unavailable');
  const kindText = text(raw.kind);
  const kind: CommandKind = kindText === 'direct_click' ? 'direct_click'
    : kindText === 'dictation' ? 'dictation'
      : ['scroll', 'navigate', 'search', 'browser_click', 'type_text', 'tab'].includes(kindText) ? 'browser'
        : 'unknown';
  const dictationHandled = raw.route === 'dictation' && raw.handled === true;
  const finalStatus = dictationHandled ? 'completed' : status;
  return {
    command_id: text(raw.command_id) || null,
    kind,
    status: finalStatus,
    message: dictationHandled ? 'Dictation accepted' : message,
    reason_code: reason ?? null,
    details: raw,
  };
}

/** Adapt the real GET /api/status ControlState + runtime snapshot to the UI model. */
export function mapBackendStatus(raw: BackendStatus): SessionState {
  const paused = raw.paused === true;
  const emergency = raw.emergency_stop === true;
  const visionStatus = mapVisionStatus(raw);
  const browserStatus = mapBrowserStatus(raw);
  const tracking = raw.tracking ?? {};
  const isTracking = tracking.active === true && visionStatus === 'available';
  const voiceResult = raw.voice?.last_command_result;
  const lastCommand = raw.voice?.last_command ? mapCommandResult(voiceResult) : null;
  const voiceStatus: ServiceStatus = 'unavailable'; // Voice input is browser-owned; the API exposes no service health field.
  const lifecycle: SessionLifecycle = emergency || raw.camera?.status === 'stopped' && browserStatus === 'stopped'
    ? 'stopped'
    : visionStatus === 'available' || browserStatus === 'available' ? 'active' : 'idle';
  const warnings: string[] = [];
  if (paused) warnings.push('paused');
  if (emergency) warnings.push('emergency_stop');
  if (visionStatus === 'unavailable' || visionStatus === 'stopped' || visionStatus === 'error') warnings.push('vision_unavailable');
  if (browserStatus !== 'available') warnings.push('browser_not_running');
  return {
    session_id: 'handsfree-runtime',
    lifecycle,
    paused,
    emergency_stopped: emergency,
    voice_status: voiceStatus,
    vision_status: visionStatus,
    browser_status: browserStatus,
    face_detected: Number(tracking.faces ?? 0) > 0,
    tracking_status: isTracking ? 'tracking' : visionStatus === 'available' ? 'lost' : 'unavailable',
    tracking_confidence: isTracking && typeof tracking.quality === 'number' ? tracking.quality : null,
    target_x: typeof raw.cursor?.nx === 'number' ? raw.cursor.nx : null,
    target_y: typeof raw.cursor?.ny === 'number' ? raw.cursor.ny : null,
    target_stable: false,
    calibrated: raw.calibration?.calibrated === true,
    cursor_control_enabled: raw.cursor_control_enabled === true,
    browser_permission_enabled: false,
    current_command_id: null,
    last_command_result: lastCommand,
    active_warnings: warnings,
    started_at: '', // ControlState does not expose a session start timestamp.
    updated_at: text(raw.server_time) || text(raw.last_updated),
  };
}

// Configurable URL remains compatible with previously saved /api/v1 base values.
const getApiRoot = (): string => {
  const configured = typeof window !== 'undefined' ? window.localStorage?.getItem('HANDSFREE_API_BASE') : null;
  return (configured || 'http://127.0.0.1:8000').replace(/\/+$/, '').replace(/\/api\/v1$/, '');
};

export const getWsUrl = (): string => {
  const configured = typeof window !== 'undefined' ? window.localStorage?.getItem('HANDSFREE_WS_URL') : null;
  const root = new URL(configured || getApiRoot());
  root.protocol = root.protocol === 'https:' ? 'wss:' : 'ws:';
  root.pathname = '/api/ws/telemetry';
  root.search = '';
  root.hash = '';
  return root.toString();
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch(`${getApiRoot()}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null) as { detail?: unknown; error?: { message?: string } } | null;
      throw new Error(body?.error?.message || (typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}: ${res.statusText}`));
    }
    return await res.json() as T;
  } catch (err: unknown) {
    if (err instanceof Error && err.name === 'AbortError') throw new Error('Backend request timed out');
    if (err instanceof Error) throw err;
    throw new Error('Unknown network error');
  } finally {
    clearTimeout(timeoutId);
  }
}

const statusRequest = async (): Promise<SessionState> => mapBackendStatus(await request<BackendStatus>('/api/status'));
const controlRequest = async (path: string, body: Record<string, boolean>): Promise<SessionActionResponse> => ({
  state: mapBackendStatus(await request<BackendStatus>(path, { method: 'POST', body: JSON.stringify(body) })),
});

export const backendApi = {
  getHealth: () => request<HealthResponse>('/health'),
  getSession: statusRequest,
  pauseSession: (paused = true) => controlRequest('/api/control/pause', { paused }),
  resumeSession: () => controlRequest('/api/control/pause', { paused: false }),
  emergencyStop: (active = true) => controlRequest('/api/control/emergency-stop', { active }),
  recoverSession: () => controlRequest('/api/control/emergency-stop', { active: false }),
  sendVoiceCommand: async (transcript: string): Promise<CommandResult> =>
    mapCommandResult(await request<unknown>('/api/voice/command', {
      method: 'POST', body: JSON.stringify({ text: transcript }),
    })),
};

export function mapReadiness(session: SessionState): ReadinessResponse {
  const services = { voice: session.voice_status, vision: session.vision_status, browser: session.browser_status };
  const safe = !session.paused && !session.emergency_stopped;
  return {
    ready: safe && (services.vision === 'available' || services.browser === 'available'),
    safe,
    partial: Object.values(services).some((value) => value !== 'available'),
    services,
  };
}

export interface BackendTelemetryEvent {
  id?: number;
  ts?: string;
  kind?: string;
  message?: string;
  reason?: string;
  data?: Record<string, unknown>;
}
export type TelemetryPacket = { type: 'history'; events: BackendTelemetryEvent[] } | { type: 'event'; event: BackendTelemetryEvent };

/** Decode the actual {type: history, events} / {type: event, event} wire packets. */
export function parseTelemetryPacket(value: unknown): TelemetryPacket | null {
  const packet = asRecord(value);
  if (packet.type === 'history') {
    return { type: 'history', events: Array.isArray(packet.events) ? packet.events.map(asRecord) as BackendTelemetryEvent[] : [] };
  }
  if (packet.type === 'event') {
    const event = asRecord(packet.event);
    if (typeof event.kind !== 'string' || typeof event.message !== 'string') return null;
    return { type: 'event', event: event as BackendTelemetryEvent };
  }
  return null;
}
