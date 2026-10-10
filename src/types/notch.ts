export type NotchState =
  | 'idle'
  | 'activating'
  | 'listening'
  | 'dictation_demo'
  | 'context_selecting'
  | 'processing_demo'
  | 'response_demo'
  | 'paused'
  | 'emergency_stop'
  | 'permission_required'
  | 'error';

export type ContextMode = 'voice' | 'dictation' | 'context';

export interface DisplayBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SelectionRegion {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  width: number;
  height: number;
}

export interface AudioVisualizerData {
  volume: number;
  peaks: number[];
  waveform: number[];
}

export interface NotchDiagnostics {
  state: NotchState;
  windowMode: 'notch' | 'context_selection_fullscreen';
  micGranted: boolean;
  reducedMotion: boolean;
  activeMode: ContextMode;
  audioReactive: boolean;
}

export interface ElectronAPI {
  resizeWindow: (width: number, height: number, animated?: boolean) => Promise<void>;
  setIgnoreMouseEvents: (ignore: boolean, forward?: boolean) => Promise<void>;
  enterContextSelectionMode: () => Promise<void>;
  exitContextSelectionMode: () => Promise<void>;
  closeApp: () => Promise<void>;
  onStateTrigger: (callback: (action: string) => void) => () => void;
  getDisplayBounds: () => Promise<DisplayBounds>;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}
