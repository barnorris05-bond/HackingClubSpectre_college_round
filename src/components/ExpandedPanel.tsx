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
  Sparkles,
  ShieldAlert,
  CheckCircle,
  XCircle,
  Loader,
  Radio,
} from 'lucide-react';

import type { CommandResult, SessionState } from '../services/backendApi';
import type { CameraStatus } from '../hooks/useHeadTracking';
import type { WakeWordStatus } from '../hooks/useWakeWord';
import type { VoiceCommandStatus, VoiceCommandResult } from '../hooks/useVoiceCommands';
import { Camera, VideoOff, RefreshCw } from 'lucide-react';

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
  // Backend integration props
  backendConnected: boolean;
  backendSession: SessionState | null;
  lastCommandResult: CommandResult | null;
  lastBlockedReason: string | null;
  onExecuteBackendCommand?: (transcript: string) => void;
  onPauseBackend?: () => void;
  onResumeBackend?: () => void;
  // Vision & Camera props
  cameraStatus: CameraStatus;
  calibrated: boolean;
  cameraError: string | null;
  cameraPreviewUrl: string | null;
  videoRef: React.RefObject<HTMLImageElement | null>;
  onStartCamera: () => void;
  onStopCamera: () => void;
  onCalibrateCamera: () => void;
  // Wake-word props
  wakeWordStatus: WakeWordStatus;
  onStartWakeWord: () => void;
  onStopWakeWord: () => void;
  // Voice command state machine props
  voiceCommandStatus: VoiceCommandStatus;
  voiceCommandResult: VoiceCommandResult | null;
  voiceCommandTranscript: string | null;
  isVoiceListening: boolean;
  onStartVoiceListening: () => void;
  onStopVoiceListening: () => void;
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
  backendConnected,
  backendSession,
  lastCommandResult,
  lastBlockedReason,
  onExecuteBackendCommand,
  onPauseBackend,
  onResumeBackend,
  cameraStatus,
  calibrated,
  cameraError,
  cameraPreviewUrl,
  videoRef,
  onStartCamera,
  onStopCamera,
  onCalibrateCamera,
  wakeWordStatus,
  onStartWakeWord,
  onStopWakeWord,
  voiceCommandStatus,
  voiceCommandResult,
  voiceCommandTranscript,
  isVoiceListening,
  onStartVoiceListening,
  onStopVoiceListening,
}) => {
  return (
    <div className="expanded-content-wrapper">
      {/* Dynamic Mode Switcher (Voice / Dictation / Context / Vision) */}
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
          <span>Context</span>
        </button>

        <button
          className={`mode-tab ${activeMode === 'vision' ? 'active' : ''}`}
          onClick={() => onSwitchMode('vision')}
        >
          <Camera className="w-3.5 h-3.5" />
          <span>Vision</span>
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
          <button
            className="disarm-btn"
            onClick={onReset}
          >
            Disarm &amp; Recover Session
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
                <span className={`pulsing-dot ${voiceCommandStatus !== 'idle' && voiceCommandStatus !== 'unavailable' ? 'active-command-dot' : ''}`} />
                <span className="status-label">
                  {voiceCommandStatus === 'wake_detected'
                    ? 'Wake Detected: "Hey Handsfree"'
                    : voiceCommandStatus === 'listening_for_command'
                    ? 'Listening for Command...'
                    : voiceCommandStatus === 'recognizing'
                    ? 'Recognizing Speech...'
                    : voiceCommandStatus === 'parsing_intent'
                    ? 'Parsing Intent...'
                    : voiceCommandStatus === 'executing'
                    ? 'Executing Action...'
                    : voiceCommandStatus === 'completed'
                    ? 'Command Completed'
                    : voiceCommandStatus === 'failed'
                    ? 'Command Failed'
                    : voiceCommandStatus === 'blocked'
                    ? 'Command Blocked'
                    : voiceCommandStatus === 'unavailable'
                    ? 'Speech Recognition Unavailable'
                    : state === 'processing_demo'
                    ? 'Synthesizing...'
                    : state === 'response_demo'
                    ? 'Response Ready'
                    : isVoiceListening
                    ? 'Voice Listening Active'
                    : 'Voice Assistant Ready'}
                </span>
              </div>

              {/* Real-time speech transcript during listening */}
              {voiceCommandTranscript && (
                <div className="voice-live-transcript">
                  <Radio className="w-3 h-3 text-sky-400 animate-pulse" />
                  <span className="transcript-text">"{voiceCommandTranscript}"</span>
                </div>
              )}

              {/* Instructions or status hint */}
              {!voiceCommandTranscript && !voiceCommandResult && !lastCommandResult && (
                <p className="status-instruction">
                  Say “Hey Handsfree” then “Open YouTube” or “Open Google”, or use the manual listener below.
                </p>
              )}

              {/* Local Voice Command Execution Result */}
              {voiceCommandResult && (
                <div
                  className={`response-box ${
                    voiceCommandResult.success ? 'completed-box' : 'failed-box'
                  }`}
                >
                  <div className="response-header-row">
                    <span
                      className={`response-tag ${
                        voiceCommandResult.success ? 'tag-completed' : 'tag-failed'
                      }`}
                    >
                      {voiceCommandResult.success ? (
                        <CheckCircle className="w-3 h-3 inline mr-1" />
                      ) : (
                        <XCircle className="w-3 h-3 inline mr-1" />
                      )}
                      Voice Action: {voiceCommandResult.intent.toUpperCase()}
                    </span>
                    {voiceCommandResult.action && (
                      <span className="action-tag">{voiceCommandResult.action}</span>
                    )}
                  </div>
                  <p className="response-text">{voiceCommandResult.message}</p>
                  {voiceCommandResult.transcript && (
                    <span className="reason-code">Heard: "{voiceCommandResult.transcript}"</span>
                  )}
                </div>
              )}

              {/* Real Backend Command Result & Blocked Reason */}
              {lastCommandResult && !voiceCommandResult && (
                <div
                  className={`response-box ${
                    lastCommandResult.status === 'blocked'
                      ? 'blocked-box'
                      : lastCommandResult.status === 'completed'
                      ? 'completed-box'
                      : lastCommandResult.status === 'failed'
                      ? 'failed-box'
                      : lastCommandResult.status === 'processing'
                      ? 'processing-box'
                      : ''
                  }`}
                >
                  <span
                    className={`response-tag ${
                      lastCommandResult.status === 'blocked'
                        ? 'tag-blocked'
                        : lastCommandResult.status === 'completed'
                        ? 'tag-completed'
                        : lastCommandResult.status === 'failed'
                        ? 'tag-failed'
                        : lastCommandResult.status === 'processing'
                        ? 'tag-processing'
                        : ''
                    }`}
                  >
                    Backend: {lastCommandResult.status.toUpperCase()}
                  </span>
                  <p className="response-text">{lastCommandResult.message}</p>
                  {lastCommandResult.reason_code && (
                    <span className="reason-code">Reason: {lastCommandResult.reason_code}</span>
                  )}
                  {(lastCommandResult.details && 'dry_run' in lastCommandResult.details && lastCommandResult.details.dry_run === true) ? (
                    <span className="dry-run-badge">Dry-Run Mode</span>
                  ) : null}
                </div>
              )}
            </div>
          </div>

          {/* Action Row */}
          <div className="action-row">
            {/* Voice Command Recognition Engine Controls */}
            {!isVoiceListening ? (
              <button
                className="primary-action-btn"
                onClick={onStartVoiceListening}
                title="Start Web Speech API recognition for wake word and commands"
              >
                <Radio className="w-4 h-4 text-sky-300" />
                <span>Start Voice Listener</span>
              </button>
            ) : (
              <button
                className="secondary-action-btn active-mic"
                onClick={onStopVoiceListening}
                title="Stop voice command recognition"
              >
                <Square className="w-3.5 h-3.5" />
                <span>Stop Voice Listener</span>
              </button>
            )}

            {/* Audio Visualizer Live Mic Toggle */}
            {!isAudioReactive ? (
              <button
                className="secondary-action-btn"
                onClick={onStartMic}
                title="Enable audio frequency reactive visualizer"
              >
                <Mic className="w-3.5 h-3.5" />
                <span>Visualizer</span>
              </button>
            ) : (
              <button
                className="secondary-action-btn active-mic"
                onClick={onStopMic}
                title="Stop audio frequency visualizer"
              >
                <Square className="w-3.5 h-3.5" />
                <span>Stop Visualizer</span>
              </button>
            )}

            {/* Quick action button: Open YouTube test */}
            <button
              className="secondary-action-btn"
              onClick={async () => {
                if (window.electronAPI?.openUrl) {
                  await window.electronAPI.openUrl('https://www.youtube.com');
                }
              }}
              title="Test validated browser opening IPC (YouTube)"
            >
              <Sparkles className="w-3.5 h-3.5 text-red-400" />
              <span>Open YouTube</span>
            </button>
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
            <button
              className="primary-action-btn"
              onClick={() => {
                if (backendConnected && onExecuteBackendCommand) {
                  onExecuteBackendCommand('dictate Hello from Handsfree Notch');
                } else {
                  onTriggerProcessing();
                }
              }}
            >
              <Play className="w-3.5 h-3.5" />
              <span>{backendConnected ? 'Send Dictation to Backend' : 'Simulate Text Commit'}</span>
            </button>
          </div>
        </div>
      ) : activeMode === 'context' ? (
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
      ) : (
        /* Vision / Head Tracking Mode View */
        <div className="vision-mode-view">
          <div className="vision-preview-row">
            <div className="webcam-feed-container">
              <img
                ref={videoRef}
                src={cameraPreviewUrl ?? undefined}
                className={`webcam-video ${cameraPreviewUrl ? '' : 'hidden'}`}
                alt="Backend camera preview"
              />
              {cameraStatus === 'inactive' && (
                <div className="webcam-inactive-placeholder">
                  <VideoOff className="w-6 h-6 text-stone-400" />
                  <span>Webcam Inactive</span>
                </div>
              )}
              {cameraStatus === 'starting' && (
                <div className="webcam-inactive-placeholder">
                  <RefreshCw className="w-6 h-6 text-blue-400 animate-spin" />
                  <span>Starting Camera...</span>
                </div>
              )}
              {(cameraStatus === 'camera_error' || cameraStatus === 'model_error') && (
                <div className="webcam-inactive-placeholder">
                  <VideoOff className="w-6 h-6 text-stone-400" />
                  <span>Vision unavailable</span>
                </div>
              )}
            </div>

            <div className="vision-metrics-column">
              <div className="vision-status-badge">
                <span className={`vision-indicator-dot ${cameraStatus === 'tracking_active' ? 'tracking' : cameraStatus === 'loading_model' ? 'loading' : cameraStatus === 'face_detected' ? 'detected' : ''}`} />
                <span className="vision-status-text">
                  {cameraStatus === 'tracking_active'
                    ? 'Head Tracking Active'
                    : cameraStatus === 'face_detected'
                    ? 'Face Detected'
                    : cameraStatus === 'active_awaiting_face'
                    ? 'Awaiting Face'
                    : cameraStatus === 'loading_model'
                    ? 'Loading Tracking Model...'
                    : cameraStatus === 'model_loaded'
                    ? 'Model Loaded'
                    : cameraStatus === 'model_error'
                    ? 'Model Load Failed'
                    : cameraStatus === 'tracking_lost'
                    ? 'Tracking Lost'
                    : cameraStatus === 'permission_denied'
                    ? 'Permission Denied'
                    : cameraStatus === 'starting'
                    ? 'Initializing'
                    : 'Inactive'}
                </span>
              </div>

              {cameraError && (
                <p className="vision-error-text">{cameraError}</p>
              )}

              <div className="vision-actions">
                {cameraStatus === 'inactive' || cameraStatus === 'permission_denied' || cameraStatus === 'camera_error' || cameraStatus === 'model_error' ? (
                  <button className="secondary-action-btn" onClick={onStartCamera}>
                    <Camera className="w-3.5 h-3.5" />
                    <span>Start Camera</span>
                  </button>
                ) : (
                  <button className="secondary-action-btn" onClick={onStopCamera}>
                    <VideoOff className="w-3.5 h-3.5" />
                    <span>Stop Camera</span>
                  </button>
                )}

                {cameraStatus === 'tracking_active' && (
                  <button
                    className={`secondary-action-btn ${calibrated ? 'calibrated-btn' : ''}`}
                    onClick={onCalibrateCamera}
                    title="Calibrate neutral center head orientation"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                    <span>{calibrated ? 'Calibrated' : 'Calibrate'}</span>
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Wake-Word Status Indicator in Expanded Panel */}
          <div className="wake-word-footer-box">
            <span className="wake-word-label">Wake Phrase:</span>
            <span className="wake-word-phrase">"Hey Handsfree"</span>
            <span className={`wake-word-badge ${wakeWordStatus}`}>
              {wakeWordStatus === 'listening'
                ? 'Monitoring'
                : wakeWordStatus === 'detected'
                ? 'Detected!'
                : wakeWordStatus === 'unavailable'
                ? 'Engine Unavailable'
                : wakeWordStatus === 'permission_denied'
                ? 'Permission Denied'
                : 'Disabled'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
