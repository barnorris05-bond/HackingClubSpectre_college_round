import React, { useEffect, useRef } from 'react';
import type { NotchState } from '../types/notch';

interface VoiceOrbProps {
  state: NotchState;
  volume: number;
  isAudioReactive: boolean;
  reducedMotion: boolean;
}

export const VoiceOrb: React.FC<VoiceOrbProps> = ({
  state,
  volume,
  isAudioReactive,
  reducedMotion,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const timeRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let active = true;

    const render = () => {
      if (!active) return;
      timeRef.current += reducedMotion ? 0.005 : 0.035;
      const t = timeRef.current;

      const width = canvas.width;
      const height = canvas.height;
      const cx = width / 2;
      const cy = height / 2;

      ctx.clearRect(0, 0, width, height);

      // Amplitude response
      const amp = isAudioReactive ? Math.max(0.08, volume * 1.5) : 0.18 + Math.sin(t * 1.5) * 0.06;

      // Color dynamics based on state
      let colorCenter = 'rgba(255, 253, 249, 0.95)'; // Warm white
      let colorInner = 'rgba(243, 235, 221, 0.75)';  // Cream
      let colorMiddle = 'rgba(198, 93, 70, 0.65)';  // Terracotta
      let colorOuter = 'rgba(105, 167, 255, 0.35)';  // Blue glow

      if (state === 'emergency_stop') {
        colorCenter = 'rgba(255, 220, 220, 0.95)';
        colorInner = 'rgba(224, 88, 79, 0.85)';
        colorMiddle = 'rgba(185, 56, 50, 0.75)';
        colorOuter = 'rgba(113, 45, 42, 0.4)';
      } else if (state === 'context_selecting') {
        colorCenter = 'rgba(225, 242, 255, 0.95)';
        colorInner = 'rgba(105, 167, 255, 0.85)';
        colorMiddle = 'rgba(64, 132, 244, 0.6)';
        colorOuter = 'rgba(30, 80, 180, 0.3)';
      } else if (state === 'processing_demo') {
        colorCenter = 'rgba(255, 255, 255, 0.95)';
        colorInner = 'rgba(232, 216, 196, 0.8)';
        colorMiddle = 'rgba(198, 93, 70, 0.7)';
        colorOuter = 'rgba(105, 167, 255, 0.45)';
      }

      // Outer liquid rings
      const ringCount = 3;
      for (let i = 0; i < ringCount; i++) {
        const ringRadius = 26 + i * 8 + amp * 18;
        const ringAlpha = Math.max(0, 0.45 - i * 0.12 - amp * 0.1);

        ctx.beginPath();
        ctx.arc(cx, cy, ringRadius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(105, 167, 255, ${ringAlpha})`;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }

      // Layer 1: Outer glowing halo
      const haloRadius = 32 + amp * 14;
      const haloGrad = ctx.createRadialGradient(cx, cy, 2, cx, cy, haloRadius);
      haloGrad.addColorStop(0, colorMiddle);
      haloGrad.addColorStop(0.7, colorOuter);
      haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

      ctx.beginPath();
      ctx.arc(cx, cy, haloRadius, 0, Math.PI * 2);
      ctx.fillStyle = haloGrad;
      ctx.fill();

      // Layer 2: Organic morphing core (using sinusoidal displacement)
      const coreBaseRadius = 18 + amp * 8;
      const points = 12;
      ctx.beginPath();

      for (let i = 0; i <= points; i++) {
        const angle = (i / points) * Math.PI * 2;
        const deform = Math.sin(angle * 3 + t * 2) * (2 + amp * 6) + Math.cos(angle * 2 - t) * (1.5 + amp * 4);
        const r = coreBaseRadius + (reducedMotion ? 0 : deform);
        const px = cx + Math.cos(angle) * r;
        const py = cy + Math.sin(angle) * r;

        if (i === 0) {
          ctx.moveTo(px, py);
        } else {
          ctx.lineTo(px, py);
        }
      }
      ctx.closePath();

      const coreGrad = ctx.createRadialGradient(
        cx - 2,
        cy - 2,
        1,
        cx,
        cy,
        coreBaseRadius + 4
      );
      coreGrad.addColorStop(0, colorCenter);
      coreGrad.addColorStop(0.4, colorInner);
      coreGrad.addColorStop(0.85, colorMiddle);
      coreGrad.addColorStop(1, 'rgba(0,0,0,0)');

      ctx.fillStyle = coreGrad;
      ctx.fill();

      // Layer 3: Specular gloss highlight
      ctx.beginPath();
      ctx.arc(cx - 4, cy - 4, 4 + amp * 2, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
      ctx.fill();

      if (state !== 'idle') {
        animFrameRef.current = requestAnimationFrame(render);
      }
    };

    render();

    return () => {
      active = false;
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
    };
  }, [state, volume, isAudioReactive, reducedMotion]);

  return (
    <div className="voice-orb-container">
      <canvas
        ref={canvasRef}
        width={96}
        height={96}
        className="voice-orb-canvas"
      />
    </div>
  );
};
