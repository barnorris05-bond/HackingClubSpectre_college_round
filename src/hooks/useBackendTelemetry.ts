import { useState, useEffect, useRef, useCallback } from 'react';
import {
  backendApi,
  getWsUrl,
  mapReadiness,
  parseTelemetryPacket,
  type SessionState,
  type CommandResult,
  type ReadinessResponse,
} from '../services/backendApi';

export type BackendConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export interface UseBackendTelemetryReturn {
  connectionStatus: BackendConnectionStatus;
  session: SessionState | null;
  readiness: ReadinessResponse | null;
  lastCommandResult: CommandResult | null;
  lastBlockedReason: string | null;
  backendError: string | null;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  emergencyStop: () => Promise<void>;
  recover: () => Promise<SessionState | null>;
  executeCommand: (transcript: string, execute?: boolean) => Promise<CommandResult | null>;
  refreshState: () => Promise<void>;
}

export function useBackendTelemetry(): UseBackendTelemetryReturn {
  const [connectionStatus, setConnectionStatus] = useState<BackendConnectionStatus>('connecting');
  const [session, setSession] = useState<SessionState | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [lastCommandResult, setLastCommandResult] = useState<CommandResult | null>(null);
  const [lastBlockedReason, setLastBlockedReason] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const isMountedRef = useRef(true);

  const refreshState = useCallback(async () => {
    try {
      const [, sess] = await Promise.all([backendApi.getHealth(), backendApi.getSession()]);
      if (isMountedRef.current) {
        setSession(sess);
        setReadiness(mapReadiness(sess));
        setBackendError(null);
        if (sess.last_command_result) {
          setLastCommandResult(sess.last_command_result);
          setLastBlockedReason(sess.last_command_result.status === 'blocked'
            ? sess.last_command_result.message
            : null);
        }
      }
    } catch (err: unknown) {
      if (isMountedRef.current) setBackendError(err instanceof Error ? err.message : 'Backend unavailable');
    }
  }, []);

  const handleTelemetryEvent = useCallback((event: { kind?: string; message?: string }) => {
    const kind = event.kind || '';
    const message = event.message || '';
    if (kind === 'safety' || kind === 'control' || kind === 'browser' || kind === 'voice' || kind === 'vision') {
      // Backend events are {id, ts, kind, message, data}, not EventEnvelope.
      // The status endpoint is authoritative for safety and last-command state.
      refreshState();
    }
    if (kind === 'system' && message.includes('error')) setBackendError(message);
  }, [refreshState]);

  const connectWebSocket = useCallback(() => {
    if (!isMountedRef.current) return;

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    try {
      const wsUrl = getWsUrl();
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      setConnectionStatus('connecting');

      ws.onopen = () => {
        if (!isMountedRef.current) return;
        setConnectionStatus('connected');
        setBackendError(null);
        refreshState();
      };

      ws.onmessage = (event) => {
        if (!isMountedRef.current) return;
        try {
          const packet = parseTelemetryPacket(JSON.parse(event.data));
          if (!packet) return;
          if (packet.type === 'history') {
            // The backend sends recent raw telemetry here. Initial status was
            // already fetched on socket open; parsing the history is sufficient.
            void packet.events.length;
          } else {
            handleTelemetryEvent(packet.event);
          }
        } catch {
          // Non-JSON or malformed packet
        }
      };

      ws.onerror = () => {
        if (!isMountedRef.current) return;
        setConnectionStatus('disconnected');
      };

      ws.onclose = () => {
        if (!isMountedRef.current) return;
        setConnectionStatus('disconnected');
        wsRef.current = null;
        // Reconnect after 3 seconds
        if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = setTimeout(() => {
          if (isMountedRef.current) {
            connectWebSocket();
          }
        }, 3000);
      };
    } catch {
      setConnectionStatus('disconnected');
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = setTimeout(() => {
        if (isMountedRef.current) connectWebSocket();
      }, 3000);
    }
  }, [handleTelemetryEvent, refreshState]);

  useEffect(() => {
    isMountedRef.current = true;
    connectWebSocket();

    // Initial check and periodic health poll
    refreshState();
    pollIntervalRef.current = setInterval(() => {
      refreshState();
    }, 5000);

    return () => {
      isMountedRef.current = false;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [connectWebSocket, refreshState]);

  const pause = useCallback(async () => {
    try {
      const res = await backendApi.pauseSession(true);
      setSession(res.state);
      setBackendError(null);
    } catch (err: unknown) {
      setBackendError(err instanceof Error ? err.message : 'Failed to pause session');
    }
  }, []);

  const resume = useCallback(async () => {
    try {
      const res = await backendApi.resumeSession();
      setSession(res.state);
      setBackendError(null);
    } catch (err: unknown) {
      setBackendError(err instanceof Error ? err.message : 'Failed to resume session');
    }
  }, []);

  const emergencyStop = useCallback(async () => {
    try {
      const res = await backendApi.emergencyStop();
      setSession(res.state);
      setBackendError(null);
    } catch (err: unknown) {
      setBackendError(err instanceof Error ? err.message : 'Failed to trigger emergency stop');
    }
  }, []);

  const recover = useCallback(async () => {
    try {
      const res = await backendApi.recoverSession();
      setSession(res.state);
      setBackendError(null);
      // Clearing emergency stop does not resume or arm cursor control.
      return res.state;
    } catch (err: unknown) {
      setBackendError(err instanceof Error ? err.message : 'Failed to recover session');
      return null;
    }
  }, []);

  const executeCommand = useCallback(async (transcript: string, execute = true): Promise<CommandResult | null> => {
    if (!execute) {
      const result: CommandResult = {
        command_id: `voice-not-executed-${Date.now()}`,
        kind: 'unknown',
        status: 'failed',
        message: 'The backend does not expose a non-executing voice command endpoint.',
        reason_code: 'execution_not_supported',
        details: { transcript },
      };
      setLastCommandResult(result);
      setBackendError(result.message);
      return result;
    }
    try {
      const result = await backendApi.sendVoiceCommand(transcript);
      setLastCommandResult(result);
      if (result.status === 'blocked') {
        setLastBlockedReason(result.message || result.reason_code || 'Action blocked by safety policy');
      } else {
        setLastBlockedReason(null);
      }
      setBackendError(null);
      return result;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Command execution failed';
      setBackendError(msg);
      return null;
    }
  }, []);

  return {
    connectionStatus,
    session,
    readiness,
    lastCommandResult,
    lastBlockedReason,
    backendError,
    pause,
    resume,
    emergencyStop,
    recover,
    executeCommand,
    refreshState,
  };
}
