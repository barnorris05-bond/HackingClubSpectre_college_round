import React from 'react';

interface WaveformProps {
  waveform: number[];
  isListening: boolean;
  reducedMotion: boolean;
}

export const Waveform: React.FC<WaveformProps> = ({
  waveform,
  isListening,
  reducedMotion,
}) => {
  // If not listening, show subtle flat bar activity or idle dots
  const bars = isListening
    ? waveform.slice(0, 24)
    : [0.15, 0.2, 0.35, 0.2, 0.4, 0.6, 0.3, 0.2, 0.15];

  return (
    <div className="waveform-container" aria-label="Audio Waveform Visualization">
      {bars.map((val, idx) => {
        const height = Math.max(4, Math.min(28, val * 32));
        return (
          <div
            key={idx}
            className={`waveform-bar ${isListening ? 'active' : 'idle'}`}
            style={{
              height: `${height}px`,
              transition: reducedMotion ? 'none' : 'height 80ms ease-out',
            }}
          />
        );
      })}
    </div>
  );
};
