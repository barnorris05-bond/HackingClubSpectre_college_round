import { useState, useCallback, useEffect, useRef } from 'react';
import type { NotchState, ContextMode } from '../types/notch';

export function useNotchState() {
  const [state, setState] = useState<NotchState>('idle');
  const [mode, setMode] = useState<ContextMode>('voice');
  const [diagnosticOpen, setDiagnosticOpen] = useState(false);
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const triggerWake = useCallback(() => {
    if (state === 'idle') {
      setState('activating');
      clearTimer();
      // Fluid activation sequence: 700ms activation morph -> listening
      timerRef.current = setTimeout(() => {
        setState('listening');
      }, 700);
    } else if (state === 'emergency_stop') {
      // Disarm emergency stop safely back to idle
      setState('idle');
    } else {
      // Return to idle from any expanded/active state
      clearTimer();
      setState('idle');
    }
  }, [state, clearTimer]);

  const setExplicitState = useCallback((newState: NotchState) => {
    clearTimer();
    setState(newState);
  }, [clearTimer]);

  const switchMode = useCallback((newMode: ContextMode) => {
    setMode(newMode);
    clearTimer();
    if (newMode === 'voice') {
      setState('listening');
    } else if (newMode === 'dictation') {
      setState('dictation_demo');
    } else if (newMode === 'context') {
      setState('context_selecting');
    }
  }, [clearTimer]);

  const triggerProcessingDemo = useCallback(() => {
    clearTimer();
    setState('processing_demo');
    timerRef.current = setTimeout(() => {
      setState('response_demo');
    }, 2400);
  }, [clearTimer]);

  const triggerEmergencyStop = useCallback(() => {
    clearTimer();
    setState('emergency_stop');
  }, [clearTimer]);

  const resetToIdle = useCallback(() => {
    clearTimer();
    setState('idle');
  }, [clearTimer]);

  useEffect(() => {
    return () => clearTimer();
  }, [clearTimer]);

  return {
    state,
    mode,
    diagnosticOpen,
    setDiagnosticOpen,
    triggerWake,
    setExplicitState,
    switchMode,
    triggerProcessingDemo,
    triggerEmergencyStop,
    resetToIdle,
  };
}
