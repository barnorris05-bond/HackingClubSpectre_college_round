import React, { useRef, useEffect, useState, useCallback } from 'react';
import type { SelectionRegion } from '../types/notch';
import { Check, X, Crop, Move } from 'lucide-react';

interface ContextSelectionProps {
  onComplete: (region: SelectionRegion) => void;
  onCancel: () => void;
  reducedMotion: boolean;
}

interface Point {
  x: number;
  y: number;
  time: number;
}

export const ContextSelection: React.FC<ContextSelectionProps> = ({
  onComplete,
  onCancel,
  reducedMotion,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const trailPointsRef = useRef<Point[]>([]);
  const animFrameRef = useRef<number | null>(null);

  const [isDragging, setIsDragging] = useState(false);
  const [startPos, setStartPos] = useState<{ x: number; y: number } | null>(null);
  const [currentPos, setCurrentPos] = useState<{ x: number; y: number } | null>(null);
  const [capturedRegion, setCapturedRegion] = useState<SelectionRegion | null>(null);

  // Set up canvas sizing
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };

    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);

  // Trail animation loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let active = true;

    const render = () => {
      if (!active) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const now = Date.now();
      // Drop points older than 450ms
      trailPointsRef.current = trailPointsRef.current.filter((p) => now - p.time < 450);

      const points = trailPointsRef.current;

      // Draw restrained glowing blue cursor trail
      if (points.length > 1 && !reducedMotion) {
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);

        for (let i = 1; i < points.length; i++) {
          const xc = (points[i].x + points[i - 1].x) / 2;
          const yc = (points[i].y + points[i - 1].y) / 2;
          ctx.quadraticCurveTo(points[i - 1].x, points[i - 1].y, xc, yc);
        }

        ctx.strokeStyle = 'rgba(105, 167, 255, 0.4)';
        ctx.lineWidth = 4;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.shadowColor = 'rgba(105, 167, 255, 0.7)';
        ctx.shadowBlur = 12;
        ctx.stroke();

        // Inner crisp line
        ctx.shadowBlur = 0;
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)';
        ctx.stroke();
      }

      animFrameRef.current = requestAnimationFrame(render);
    };

    render();

    return () => {
      active = false;
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
    };
  }, [reducedMotion]);

  const handlePointerDown = (e: React.PointerEvent) => {
    if (capturedRegion) return;
    setIsDragging(true);
    setStartPos({ x: e.clientX, y: e.clientY });
    setCurrentPos({ x: e.clientX, y: e.clientY });
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!reducedMotion) {
      trailPointsRef.current.push({
        x: e.clientX,
        y: e.clientY,
        time: Date.now(),
      });
    }

    if (isDragging) {
      setCurrentPos({ x: e.clientX, y: e.clientY });
    }
  };

  const handlePointerUp = () => {
    if (isDragging && startPos && currentPos) {
      setIsDragging(false);
      const minX = Math.min(startPos.x, currentPos.x);
      const minY = Math.min(startPos.y, currentPos.y);
      const width = Math.abs(currentPos.x - startPos.x);
      const height = Math.abs(currentPos.y - startPos.y);

      if (width > 24 && height > 24) {
        setCapturedRegion({
          startX: minX,
          startY: minY,
          endX: minX + width,
          endY: minY + height,
          width,
          height,
        });
      } else {
        setStartPos(null);
        setCurrentPos(null);
      }
    }
  };

  // Keyboard escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCancel();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel]);

  return (
    <div
      className="context-selection-fullscreen"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      <canvas ref={canvasRef} className="trail-canvas" />

      {/* Instruction Badge */}
      {!capturedRegion && (
        <div className="selection-instruction-pill">
          <Crop className="w-4 h-4 text-blue-400" />
          <span>Click and drag across any area of your screen to capture context</span>
          <kbd className="selection-kbd">ESC to cancel</kbd>
        </div>
      )}

      {/* Active dragging box */}
      {isDragging && startPos && currentPos && (
        <div
          className="selection-box-active"
          style={{
            left: Math.min(startPos.x, currentPos.x),
            top: Math.min(startPos.y, currentPos.y),
            width: Math.abs(currentPos.x - startPos.x),
            height: Math.abs(currentPos.y - startPos.y),
          }}
        >
          <div className="selection-box-corner top-left" />
          <div className="selection-box-corner top-right" />
          <div className="selection-box-corner bottom-left" />
          <div className="selection-box-corner bottom-right" />
          <div className="selection-dim-indicator">
            {Math.round(Math.abs(currentPos.x - startPos.x))} &times;{' '}
            {Math.round(Math.abs(currentPos.y - startPos.y))} px
          </div>
        </div>
      )}

      {/* Captured Confirmation Card */}
      {capturedRegion && (
        <div
          className="selection-confirmation-card"
          style={{
            left: Math.max(20, Math.min(window.innerWidth - 340, capturedRegion.startX)),
            top: Math.min(window.innerHeight - 120, capturedRegion.endY + 14),
          }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <div className="confirmation-header">
            <span className="confirmation-title">Context Captured (Demo)</span>
            <span className="confirmation-dims">
              {capturedRegion.width} &times; {capturedRegion.height} px
            </span>
          </div>
          <p className="confirmation-note">
            Visual area marked. AI model processing simulated for prototype.
          </p>
          <div className="confirmation-actions">
            <button
              className="confirm-btn cancel"
              onClick={onCancel}
              title="Cancel Selection"
            >
              <X className="w-4 h-4" /> Cancel
            </button>
            <button
              className="confirm-btn confirm"
              onClick={() => onComplete(capturedRegion)}
              title="Confirm Selection"
            >
              <Check className="w-4 h-4" /> Confirm Area
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
