import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useNotchState } from './hooks/useNotchState';
import { useAudioAnalyser } from './hooks/useAudioAnalyser';
import { useReducedMotion } from './hooks/useReducedMotion';
import { useBackendTelemetry } from './hooks/useBackendTelemetry';
import { useHeadTracking } from './hooks/useHeadTracking';
import { useWakeWord } from './hooks/useWakeWord';
import { useVoiceCommands } from './hooks/useVoiceCommands';
import { Notch } from './components/Notch';
import { ExpandedPanel } from './components/ExpandedPanel';
import { StateIndicator } from './components/StateIndicator';
import { ContextSelection } from './components/ContextSelection';
import { LiquidWakeOverlay } from './components/LiquidWakeOverlay';
import type { NotchDiagnostics, SelectionRegion } from './types/notch';
import './styles/notch.css';

export const App: React.FC = () => {
  const {
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
  } = useNotchState();

  const {
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
  } = useBackendTelemetry();

  const {
    isListening,
    hasPermission,
    audioData,
    startListening,
    stopListening,
  } = useAudioAnalyser();

  const reducedMotion = useReducedMotion();

  // Feature 1: Liquid Glass Desktop Wake Overlay on application startup
  const [isWakingUp, setIsWakingUp] = useState<boolean>(true);

  useEffect(() => {
    if (window.electronAPI) {
      window.electronAPI.enterWakeOverlay().catch(() => {});
    }
  }, []);

  const handleWakeAnimationEnd = useCallback(() => {
    setIsWakingUp(false);
    if (window.electronAPI) {
      window.electronAPI.exitWakeOverlay().catch(() => {});
    }
  }, []);

  // Feature 2: Automatic Head Tracking / Camera module
  const {
    status: cameraStatus,
    headPose,
    calibrated,
    errorMessage: cameraError,
    videoRef,
    startCamera,
    stopCamera,
    calibrate: calibrateCamera,
  } = useHeadTracking();

  // Feature 3: 'Hey Handsfree' Wake-Word Detector + Voice Commands
  const handleWakeWordDetected = useCallback(() => {
    triggerWake();
  }, [triggerWake]);

  const {
    status: wakeWordStatus,
    isListening: isWakeWordListening,
    startWakeWord,
    stopWakeWord,
  } = useWakeWord(handleWakeWordDetected);

  // Feature 4: Voice Command Execution
  const {
    status: voiceCommandStatus,
    lastResult: voiceCommandResult,
    lastTranscript: voiceCommandTranscript,
    isListening: isVoiceListening,
    startListening: startVoiceListening,
    stopListening: stopVoiceListening,
  } = useVoiceCommands(handleWakeWordDetected);

  // Synchronize backend emergency stop & pause states with notch visual states
  useEffect(() => {
    if (session?.emergency_stopped && state !== 'emergency_stop') {
      setExplicitState('emergency_stop');
    }
  }, [session?.emergency_stopped, state, setExplicitState]);

  // Handle resizing the native Electron BrowserWindow based on UI state
  useEffect(() => {
    if (!window.electronAPI || isWakingUp) return;

    if (state === 'context_selecting') {
      window.electronAPI.enterContextSelectionMode();
    } else if (state === 'idle') {
      window.electronAPI.resizeWindow(224, 48, !reducedMotion);
    } else {
      // Expanded state dimensions
      const height = mode === 'vision' ? 380 : diagnosticOpen ? 370 : 260;
      window.electronAPI.resizeWindow(420, height, !reducedMotion);
    }
  }, [state, diagnosticOpen, reducedMotion, isWakingUp, mode]);

  // Global hotkey trigger listening from main process
  useEffect(() => {
    if (!window.electronAPI) return;
    const cleanup = window.electronAPI.onStateTrigger((action) => {
      if (action === 'toggle-wake') {
        triggerWake();
      }
    });
    return cleanup;
  }, [triggerWake]);

  // Context Selection callback
  const handleSelectionComplete = (region: SelectionRegion) => {
    if (window.electronAPI) {
      window.electronAPI.exitContextSelectionMode();
    }
    setExplicitState('listening');
  };

  const handleSelectionCancel = () => {
    if (window.electronAPI) {
      window.electronAPI.exitContextSelectionMode();
    }
    resetToIdle();
  };

  const handleEmergencyStop = () => {
    triggerEmergencyStop();
    if (connectionStatus === 'connected') {
      emergencyStop().catch(() => {});
    }
  };

  const handleRecover = () => {
    if (connectionStatus === 'connected') {
      recover().catch(() => {});
    }
    resetToIdle();
  };

  const isExpanded = state !== 'idle' && state !== 'context_selecting' && !isWakingUp;

  const diagnostics: NotchDiagnostics = {
    state,
    windowMode: state === 'context_selecting' ? 'context_selection_fullscreen' : 'notch',
    micGranted: !!hasPermission && isListening,
    reducedMotion,
    activeMode: mode,
    audioReactive: isListening,
    backendConnected: connectionStatus === 'connected',
    backendPaused: !!session?.paused,
    backendEmergencyStopped: !!session?.emergency_stopped,
    backendVoiceStatus: session?.voice_status || 'unavailable',
    sessionId: session?.session_id,
    cameraStatus,
    headPoseConfidence: headPose?.confidence,
    wakeWordStatus,
  };

  return (
    <>
      {/* Liquid Glass Desktop Wake Overlay */}
      {isWakingUp && (
        <LiquidWakeOverlay
          onAnimationEnd={handleWakeAnimationEnd}
          reducedMotion={reducedMotion}
        />
      )}

      {state === 'context_selecting' ? (
        <ContextSelection
          onComplete={handleSelectionComplete}
          onCancel={handleSelectionCancel}
          reducedMotion={reducedMotion}
        />
      ) : (
        <div
          className={`notch-island-shell ${isExpanded ? 'expanded' : ''} ${
            state === 'emergency_stop' ? 'emergency_stop' : ''
          }`}
          style={isWakingUp ? { marginTop: '14px' } : undefined}
        >
          {/* Top Notch Capsule */}
          <Notch
            state={state}
            onActivate={triggerWake}
            reducedMotion={reducedMotion}
            backendConnected={connectionStatus === 'connected'}
            backendPaused={session?.paused}
            cameraActive={cameraStatus === 'tracking_active' || cameraStatus === 'active_awaiting_face'}
            wakeWordListening={isWakeWordListening}
          />

          {/* Expanded HUD Content */}
          {isExpanded && (
            <>
              <ExpandedPanel
                state={state}
                activeMode={mode}
                onSwitchMode={switchMode}
                onTriggerProcessing={triggerProcessingDemo}
                onEmergencyStop={handleEmergencyStop}
                onReset={handleRecover}
                isAudioReactive={isListening}
                audioData={audioData}
                onStartMic={startListening}
                onStopMic={stopListening}
                hasMicPermission={hasPermission}
                reducedMotion={reducedMotion}
                backendConnected={connectionStatus === 'connected'}
                backendSession={session}
                lastCommandResult={lastCommandResult}
                lastBlockedReason={lastBlockedReason}
                onExecuteBackendCommand={(transcript) => executeCommand(transcript, true)}
                onPauseBackend={pause}
                onResumeBackend={resume}
                onRecoverBackend={handleRecover}
                cameraStatus={cameraStatus}
                headPose={headPose}
                calibrated={calibrated}
                cameraError={cameraError}
                videoRef={videoRef}
                onStartCamera={startCamera}
                onStopCamera={stopCamera}
                onCalibrateCamera={calibrateCamera}
                wakeWordStatus={wakeWordStatus}
                onStartWakeWord={startWakeWord}
                onStopWakeWord={stopWakeWord}
                voiceCommandStatus={voiceCommandStatus}
                voiceCommandResult={voiceCommandResult}
                voiceCommandTranscript={voiceCommandTranscript}
                isVoiceListening={isVoiceListening}
                onStartVoiceListening={startVoiceListening}
                onStopVoiceListening={stopVoiceListening}
              />

              <div style={{ padding: '0 18px 14px 18px' }}>
                <StateIndicator
                  diagnostics={diagnostics}
                  onReset={handleRecover}
                  onEmergencyStop={handleEmergencyStop}
                  onToggleDiagnostics={() => setDiagnosticOpen(!diagnosticOpen)}
                  isDiagnosticOpen={diagnosticOpen}
                />
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
};
export default App;
