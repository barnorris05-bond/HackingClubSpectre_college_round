import { useState, useEffect, useRef, useCallback } from 'react';
import {
  backendApi,
  getWsUrl,
  type SessionState,
  type CommandResult,
  type EventEnvelope,
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
  recover: () => Promise<void>;
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
      const [sess, ready] = await Promise.all([
        backendApi.getSession().catch(() => null),
        backendApi.getReadiness().catch(() => null),
      ]);
      if (isMountedRef.current) {
        if (sess) setSession(sess);
        if (ready) setReadiness(ready);
      }
    } catch {
      // Backend may be offline
    }
  }, []);

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
          const envelope: EventEnvelope = JSON.parse(event.data);
          const { type, data } = envelope;

          if (type === 'session.state' && data?.state) {
            // Authoritative full session snapshot
            setSession(data.state as SessionState);
          } else if (type === 'session.paused' || type === 'session.resumed' || type === 'session.emergency_stop') {
            // Partial state events - refresh full session to avoid stale state
            refreshState();
          } else if (type === 'command.completed') {
            const result = data as unknown as CommandResult;
            setLastCommandResult(result);
            setLastBlockedReason(null);
          } else if (type === 'command.blocked') {
            const result = data as unknown as CommandResult;
            setLastCommandResult(result);
            setLastBlockedReason(result.message || result.reason_code || 'Action blocked by safety policy');
          } else if (type === 'command.failed') {
            const result = data as unknown as CommandResult;
            setLastCommandResult(result);
          } else if (type === 'command.processing') {
            const result = data as unknown as CommandResult;
            setLastCommandResult(result);
          } else if (type === 'system.error') {
            setBackendError(String(data?.message || 'Backend system error'));
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
  }, [refreshState]);

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
      const res = await backendApi.pauseSession();
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
      setLastBlockedReason(null);
    } catch (err: unknown) {
      setBackendError(err instanceof Error ? err.message : 'Failed to recover session');
    }
  }, []);

  const executeCommand = useCallback(async (transcript: string, execute = true): Promise<CommandResult | null> => {
    try {
      const result = await backendApi.sendVoiceCommand(transcript, execute);
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
