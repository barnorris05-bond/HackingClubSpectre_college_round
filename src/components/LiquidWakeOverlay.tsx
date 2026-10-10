import React, { useEffect, useRef } from 'react';

interface LiquidWakeOverlayProps {
  onAnimationEnd: () => void;
  reducedMotion: boolean;
}

export const LiquidWakeOverlay: React.FC<LiquidWakeOverlayProps> = ({
  onAnimationEnd,
  reducedMotion,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(Date.now());

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (reducedMotion) {
      // In reduced motion mode, end quickly
      const t = setTimeout(onAnimationEnd, 300);
      return () => clearTimeout(t);
    }

    const duration = 1400; // 1.4 seconds total wake sequence
    let isCancelled = false;

    const render = () => {
      if (isCancelled) return;

      const elapsed = Date.now() - startTimeRef.current;
      const progress = Math.min(1, elapsed / duration);

      const width = canvas.width;
      const height = canvas.height;
      const cx = width / 2;
      const cy = 35; // Originating near the notch capsule at top-center

      ctx.clearRect(0, 0, width, height);

      // Liquid wave expansion phases:
      // 0.0 - 0.5: expansion
      // 0.5 - 1.0: gentle settle & dissolve
      const expandProgress = Math.min(1, progress * 1.8);
      const fadeProgress = progress > 0.4 ? (progress - 0.4) / 0.6 : 0;
      const overallAlpha = Math.max(0, 1 - fadeProgress);

      const maxRadius = Math.sqrt(width * width + height * height) * 0.85;
      const currentRadius = maxRadius * Math.sin((expandProgress * Math.PI) / 2);

      // Layer 1: Ambient subtle desktop refraction halo
      const haloGrad = ctx.createRadialGradient(cx, cy, 10, cx, cy, currentRadius);
      haloGrad.addColorStop(0, `rgba(105, 167, 255, ${0.12 * overallAlpha})`);
      haloGrad.addColorStop(0.3, `rgba(243, 235, 221, ${0.08 * overallAlpha})`);
      haloGrad.addColorStop(0.65, `rgba(198, 93, 70, ${0.05 * overallAlpha})`);
      haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');

      ctx.fillStyle = haloGrad;
      ctx.fillRect(0, 0, width, height);

      // Layer 2: Expanding iridescent ripple rings
      const ringCount = 4;
      for (let i = 0; i < ringCount; i++) {
        const ringOffset = i * 0.12;
        const ringProgress = Math.max(0, expandProgress - ringOffset);
        if (ringProgress > 0) {
          const r = currentRadius * (1 - i * 0.18);
          const ringAlpha = (1 - ringProgress) * 0.28 * overallAlpha;

          ctx.beginPath();
          ctx.arc(cx, cy, Math.max(5, r), 0, Math.PI * 2);
          ctx.lineWidth = 1.5 + (ringCount - i) * 0.8;
          ctx.strokeStyle = i % 2 === 0
            ? `rgba(105, 167, 255, ${ringAlpha})`
            : `rgba(255, 253, 249, ${ringAlpha * 1.2})`;
          ctx.stroke();
        }
      }

      // Layer 3: Central notch awakening glow
      const notchGlowRadius = 120 * (1 - expandProgress * 0.4);
      const notchGlow = ctx.createRadialGradient(cx, cy, 5, cx, cy, notchGlowRadius);
      notchGlow.addColorStop(0, `rgba(255, 253, 249, ${0.4 * overallAlpha})`);
      notchGlow.addColorStop(0.5, `rgba(105, 167, 255, ${0.2 * overallAlpha})`);
      notchGlow.addColorStop(1, 'rgba(0, 0, 0, 0)');

      ctx.fillStyle = notchGlow;
      ctx.beginPath();
      ctx.arc(cx, cy, notchGlowRadius, 0, Math.PI * 2);
      ctx.fill();

      if (progress < 1) {
        animFrameRef.current = requestAnimationFrame(render);
      } else {
        onAnimationEnd();
      }
    };

    animFrameRef.current = requestAnimationFrame(render);

    return () => {
      isCancelled = true;
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
    };
  }, [onAnimationEnd, reducedMotion]);

  return (
    <div className="liquid-wake-overlay-container">
      <canvas ref={canvasRef} className="liquid-wake-canvas" />
    </div>
  );
};
