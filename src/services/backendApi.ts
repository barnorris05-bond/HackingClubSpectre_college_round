// Backend API Types & Contracts

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
  last_command_result: Record<string, unknown> | null;
  active_warnings: string[];
  started_at: string;
  updated_at: string;
}

export interface SessionActionResponse {
  state: SessionState;
}

export type CommandKind = 'direct_click' | 'browser' | 'dictation' | 'unknown';
export type CommandStatus = 'received' | 'processing' | 'completed' | 'blocked' | 'failed';

export interface CommandResult {
  command_id: string;
  kind: CommandKind;
  status: CommandStatus;
  message: string;
  reason_code?: string | null;
  details: Record<string, unknown>;
}

export type EventType =
  | 'session.state'
  | 'session.paused'
  | 'session.resumed'
  | 'session.emergency_stop'
  | 'voice.state'
  | 'voice.transcript'
  | 'voice.error'
  | 'command.received'
  | 'command.processing'
  | 'command.completed'
  | 'command.blocked'
  | 'command.failed'
  | 'vision.state'
  | 'vision.face_detected'
  | 'vision.tracking_lost'
  | 'vision.calibration'
  | 'cursor.state'
  | 'browser.state'
  | 'browser.action'
  | 'evaluation.updated'
  | 'system.warning'
  | 'system.error';

export interface EventEnvelope {
  type: EventType;
  timestamp: string;
  session_id?: string | null;
  sequence: number;
  data: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface ReadinessResponse {
  ready: boolean;
  safe: boolean;
  partial: boolean;
  services: {
    voice: ServiceStatus;
    vision: ServiceStatus;
    browser: ServiceStatus;
  };
}

export interface BackendErrorBody {
  code: string;
  message: string;
  subsystem: string;
  recoverable: boolean;
  request_id?: string | null;
  details?: Record<string, unknown> | null;
}

export interface BackendErrorResponse {
  error: BackendErrorBody;
}

// Configurable endpoints
const getBaseUrl = (): string => {
  return (typeof window !== 'undefined' && window.localStorage?.getItem('HANDSFREE_API_BASE')) || 'http://127.0.0.1:8000/api/v1';
};

export const getWsUrl = (): string => {
  return (typeof window !== 'undefined' && window.localStorage?.getItem('HANDSFREE_WS_URL')) || 'ws://127.0.0.1:8000/api/v1/telemetry';
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 4000);

  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });

    clearTimeout(timeoutId);

    if (!res.ok) {
      let errorBody: BackendErrorResponse | null = null;
      try {
        errorBody = await res.json();
      } catch {
        // Not a JSON response
      }

      if (errorBody && errorBody.error) {
        throw new Error(errorBody.error.message || `Backend error: ${errorBody.error.code}`);
      }
      throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }

    return await res.json();
  } catch (err: unknown) {
    clearTimeout(timeoutId);
    if (err instanceof Error) {
      if (err.name === 'AbortError') {
        throw new Error('Backend request timed out');
      }
      throw err;
    }
    throw new Error('Unknown network error');
  }
}

export const backendApi = {
  getHealth: () => request<HealthResponse>('/health'),
  getReadiness: () => request<ReadinessResponse>('/ready'),
  getSession: () => request<SessionState>('/session'),
  pauseSession: () => request<SessionActionResponse>('/session/pause', { method: 'POST' }),
  resumeSession: () => request<SessionActionResponse>('/session/resume', { method: 'POST' }),
  emergencyStop: () => request<SessionActionResponse>('/session/emergency-stop', { method: 'POST' }),
  recoverSession: () => request<SessionActionResponse>('/session/recover', { method: 'POST' }),
  sendVoiceCommand: (transcript: string, execute = true) =>
    request<CommandResult>('/voice/commands', {
      method: 'POST',
      body: JSON.stringify({ transcript, execute }),
    }),
};
