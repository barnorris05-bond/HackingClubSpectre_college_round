import React, { useRef, useState } from 'react';
import type { NotchState } from '../types/notch';
import { Mic, Sparkles, ChevronDown, Radio } from 'lucide-react';

interface NotchProps {
  state: NotchState;
  onActivate: () => void;
  reducedMotion: boolean;
  backendConnected?: boolean;
  backendPaused?: boolean;
  cameraActive?: boolean;
  wakeWordListening?: boolean;
}

export const Notch: React.FC<NotchProps> = ({
  state,
  onActivate,
  reducedMotion,
  backendConnected = false,
  backendPaused = false,
  cameraActive = false,
  wakeWordListening = false,
}) => {
  const notchRef = useRef<HTMLDivElement | null>(null);
  const [mousePos, setMousePos] = useState({ x: 50, y: 50 });

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!notchRef.current) return;
    const rect = notchRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    setMousePos({ x, y });
  };

  const isIdle = state === 'idle';

  return (
    <div
      ref={notchRef}
      className={`notch-capsule ${state} ${reducedMotion ? 'reduced-motion' : ''}`}
      onPointerMove={handlePointerMove}
      onClick={isIdle ? onActivate : undefined}
      style={
        {
          '--mouse-x': `${mousePos.x}%`,
          '--mouse-y': `${mousePos.y}%`,
        } as React.CSSProperties
      }
      role="button"
      tabIndex={0}
      aria-label="Handsfree Floating Notch Capsule"
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          onActivate();
        }
      }}
    >
      {/* Specular optical highlight overlay */}
      <div className="notch-specular-highlight" />

      {/* Capsule Content when in Idle/Compact Mode */}
      <div className="notch-content-compact">
        {/* Handsfree warm emblem dot */}
        <div className="notch-emblem">
          <span className="notch-dot terracotta" />
          <span className="notch-dot cream" />
          {cameraActive && (
            <span
              className="notch-dot camera-dot"
              title="Camera / Vision Active"
            />
          )}
          <span
            className={`notch-dot connection-dot ${backendConnected ? 'connected' : 'offline'}`}
            title={backendConnected ? 'Backend Connected (127.0.0.1:8000)' : 'Backend Offline / Standalone'}
          />
        </div>

        {/* Ready indicator */}
        <div className="notch-status-text">
          <span className="notch-brand">Handsfree</span>
          <span className={`notch-subtext ${backendPaused ? 'paused-subtext' : ''}`}>
            {backendPaused ? 'Paused' : 'Ready'}
          </span>
        </div>

        {/* Subtle mic prompt indicator */}
        <div className="notch-action-indicator">
          <Mic className="w-3.5 h-3.5 text-cream" />
        </div>
      </div>
    </div>
  );
};
