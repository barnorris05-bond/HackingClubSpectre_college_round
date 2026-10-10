import React, { useState } from 'react';
import type { NotchState, ContextMode, AudioVisualizerData } from '../types/notch';
import { VoiceOrb } from './VoiceOrb';
import { Waveform } from './Waveform';
import {
  Mic,
  FileText,
  Scan,
  Square,
  Play,
  RotateCcw,
  Sparkles,
  ArrowRight,
  ShieldAlert,
} from 'lucide-react';

interface ExpandedPanelProps {
  state: NotchState;
  activeMode: ContextMode;
  onSwitchMode: (mode: ContextMode) => void;
  onTriggerProcessing: () => void;
  onEmergencyStop: () => void;
  onReset: () => void;
  isAudioReactive: boolean;
  audioData: AudioVisualizerData;
  onStartMic: () => void;
  onStopMic: () => void;
  hasMicPermission: boolean | null;
  reducedMotion: boolean;
}

export const ExpandedPanel: React.FC<ExpandedPanelProps> = ({
  state,
  activeMode,
  onSwitchMode,
  onTriggerProcessing,
  onEmergencyStop,
  onReset,
  isAudioReactive,
  audioData,
  onStartMic,
  onStopMic,
  hasMicPermission,
  reducedMotion,
}) => {
  return (
    <div className="expanded-content-wrapper">
      {/* Dynamic Mode Switcher (Voice / Dictation / Context) */}
      <div className="mode-segmented-bar">
        <button
          className={`mode-tab ${activeMode === 'voice' ? 'active' : ''}`}
          onClick={() => onSwitchMode('voice')}
        >
          <Mic className="w-3.5 h-3.5" />
          <span>Voice</span>
        </button>

        <button
          className={`mode-tab ${activeMode === 'dictation' ? 'active' : ''}`}
          onClick={() => onSwitchMode('dictation')}
        >
          <FileText className="w-3.5 h-3.5" />
          <span>Dictation</span>
        </button>

        <button
          className={`mode-tab ${activeMode === 'context' ? 'active' : ''}`}
          onClick={() => onSwitchMode('context')}
        >
          <Scan className="w-3.5 h-3.5" />
          <span>Context HUD</span>
        </button>
      </div>

      {/* Main Mode Body */}
      {state === 'emergency_stop' ? (
        <div className="emergency-stop-view">
          <div className="emergency-icon-container">
            <ShieldAlert className="w-8 h-8 text-red-400" />
          </div>
          <div className="emergency-title">Assistant Operations Halted</div>
          <p className="emergency-desc">
            Safety interlock disengaged all simulated tasks and listeners.
          </p>
          <button className="disarm-btn" onClick={onReset}>
            Disarm &amp; Return to Idle
          </button>
        </div>
      ) : activeMode === 'voice' ? (
        <div className="voice-mode-view">
          <div className="orb-row">
            <VoiceOrb
              state={state}
              volume={audioData.volume}
              isAudioReactive={isAudioReactive}
              reducedMotion={reducedMotion}
            />

            <div className="voice-status-column">
              <div className="status-badge">
                <span className="pulsing-dot" />
                <span className="status-label">
                  {state === 'processing_demo'
                    ? 'Synthesizing...'
                    : state === 'response_demo'
                    ? 'Response Ready'
                    : 'Listening to speech...'}
                </span>
              </div>

              {state === 'listening' && (
                <p className="status-instruction">
                  Say “Summarize this paragraph” or click simulated action
                </p>
              )}

              {state === 'processing_demo' && (
                <p className="status-instruction processing-glow">
                  Analyzing acoustic intent and spatial screen context...
                </p>
              )}

              {state === 'response_demo' && (
                <div className="response-box">
                  <span className="response-tag">Simulated Response</span>
                  <p className="response-text">
                    “Captured your request. Focusing active window and preparing hands-free workflow.”
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Audio Reactivity Toggle / Live Mic Trigger */}
          <div className="action-row">
            {!isAudioReactive ? (
              <button className="primary-action-btn" onClick={onStartMic}>
                <Mic className="w-4 h-4" />
                <span>Enable Live Mic Visualizer</span>
              </button>
            ) : (
              <button className="secondary-action-btn active-mic" onClick={onStopMic}>
                <Square className="w-3.5 h-3.5" />
                <span>Stop Microphone</span>
              </button>
            )}

            {state === 'listening' && (
              <button
                className="secondary-action-btn simulate-btn"
                onClick={onTriggerProcessing}
                title="Simulate processing query"
              >
                <Sparkles className="w-3.5 h-3.5 text-amber-300" />
                <span>Simulate Query</span>
              </button>
            )}
          </div>
        </div>
      ) : activeMode === 'dictation' ? (
        <div className="dictation-mode-view">
          <div className="dictation-waveform-row">
            <Waveform
              waveform={audioData.waveform}
              isListening={true}
              reducedMotion={reducedMotion}
            />
          </div>

          <div className="dictation-transcript-box">
            <span className="dictation-tag">Live Dictation Stream (Prototype)</span>
            <p className="dictation-text">
              “Drafting response for the open draft... The liquid-glass interface remains fluid across all states.”
            </p>
          </div>

          <div className="action-row">
            <button className="secondary-action-btn" onClick={() => onSwitchMode('voice')}>
              Switch to Voice
            </button>
            <button className="primary-action-btn" onClick={onTriggerProcessing}>
              <Play className="w-3.5 h-3.5" />
              <span>Simulate Text Commit</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="context-mode-view">
          <p className="context-desc">
            Visual contextual HUD allows selecting any bounding box on your desktop with a soft blue selection trail.
          </p>

          <button
            className="primary-action-btn trigger-selection-btn"
            onClick={() => onSwitchMode('context')}
          >
            <Scan className="w-4 h-4 text-blue-400" />
            <span>Initiate Screen Region Selection</span>
          </button>
        </div>
      )}
    </div>
  );
};
