import React, { useEffect, useRef } from 'react';
import { useNotchState } from './hooks/useNotchState';
import { useAudioAnalyser } from './hooks/useAudioAnalyser';
import { useReducedMotion } from './hooks/useReducedMotion';
import { Notch } from './components/Notch';
import { ExpandedPanel } from './components/ExpandedPanel';
import { StateIndicator } from './components/StateIndicator';
import { ContextSelection } from './components/ContextSelection';
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
    isListening,
    hasPermission,
    audioData,
    startListening,
    stopListening,
  } = useAudioAnalyser();

  const reducedMotion = useReducedMotion();

  // Handle resizing the native Electron BrowserWindow based on UI state
  useEffect(() => {
    if (!window.electronAPI) return;

    if (state === 'context_selecting') {
      window.electronAPI.enterContextSelectionMode();
    } else if (state === 'idle') {
      window.electronAPI.resizeWindow(224, 48, !reducedMotion);
    } else {
      // Expanded state dimensions
      const height = diagnosticOpen ? 340 : 250;
      window.electronAPI.resizeWindow(420, height, !reducedMotion);
    }
  }, [state, diagnosticOpen, reducedMotion]);

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

  const isExpanded = state !== 'idle' && state !== 'context_selecting';

  const diagnostics: NotchDiagnostics = {
    state,
    windowMode: state === 'context_selecting' ? 'context_selection_fullscreen' : 'notch',
    micGranted: !!hasPermission && isListening,
    reducedMotion,
    activeMode: mode,
    audioReactive: isListening,
  };

  return (
    <>
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
        >
          {/* Top Notch Capsule */}
          <Notch
            state={state}
            onActivate={triggerWake}
            reducedMotion={reducedMotion}
          />

          {/* Expanded HUD Content */}
          {isExpanded && (
            <>
              <ExpandedPanel
                state={state}
                activeMode={mode}
                onSwitchMode={switchMode}
                onTriggerProcessing={triggerProcessingDemo}
                onEmergencyStop={triggerEmergencyStop}
                onReset={resetToIdle}
                isAudioReactive={isListening}
                audioData={audioData}
                onStartMic={startListening}
                onStopMic={stopListening}
                hasMicPermission={hasPermission}
                reducedMotion={reducedMotion}
              />

              <div style={{ padding: '0 18px 14px 18px' }}>
                <StateIndicator
                  diagnostics={diagnostics}
                  onReset={resetToIdle}
                  onEmergencyStop={triggerEmergencyStop}
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
