import React from 'react';
import type { NotchDiagnostics } from '../types/notch';
import {
  Activity,
  Mic,
  Volume2,
  Sparkles,
  AlertTriangle,
  RotateCcw,
  Info,
  ChevronDown,
} from 'lucide-react';

interface StateIndicatorProps {
  diagnostics: NotchDiagnostics;
  onReset: () => void;
  onEmergencyStop: () => void;
  onToggleDiagnostics: () => void;
  isDiagnosticOpen: boolean;
}

export const StateIndicator: React.FC<StateIndicatorProps> = ({
  diagnostics,
  onReset,
  onEmergencyStop,
  onToggleDiagnostics,
  isDiagnosticOpen,
}) => {
  return (
    <div className="state-indicator-root">
      {/* Secondary micro-pill for diagnostics toggle */}
      <div className="controls-row">
        <button
          className="micro-pill-btn info-btn"
          onClick={onToggleDiagnostics}
          title="Toggle Diagnostics"
        >
          <Info className="w-3.5 h-3.5" />
          <span>HUD Info</span>
          <ChevronDown
            className={`w-3 h-3 chevron ${isDiagnosticOpen ? 'open' : ''}`}
          />
        </button>

        <button
          className="micro-pill-btn emergency-btn"
          onClick={onEmergencyStop}
          title="Emergency Stop Safety Trigger"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>Stop</span>
        </button>

        <button
          className="micro-pill-btn reset-btn"
          onClick={onReset}
          title="Reset to Idle"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      </div>

      {isDiagnosticOpen && (
        <div className="diagnostics-drawer">
          <div className="diagnostic-item">
            <span className="diag-label">State:</span>
            <span className="diag-value highlight">{diagnostics.state}</span>
          </div>
          <div className="diagnostic-item">
            <span className="diag-label">Window:</span>
            <span className="diag-value">{diagnostics.windowMode}</span>
          </div>
          <div className="diagnostic-item">
            <span className="diag-label">Microphone:</span>
            <span className={`diag-value ${diagnostics.micGranted ? 'granted' : 'dormant'}`}>
              {diagnostics.micGranted ? 'Active Hardware' : 'Simulation Mode'}
            </span>
          </div>
          <div className="diagnostic-item">
            <span className="diag-label">Motion:</span>
            <span className="diag-value">
              {diagnostics.reducedMotion ? 'Reduced' : 'Full Liquid Dynamics'}
            </span>
          </div>
          <div className="diagnostic-note">
            Prototype telemetry. No external network transmission.
          </div>
        </div>
      )}
    </div>
  );
};
