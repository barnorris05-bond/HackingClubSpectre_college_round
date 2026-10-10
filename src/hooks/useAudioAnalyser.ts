import { useState, useEffect, useRef, useCallback } from 'react';
import type { AudioVisualizerData } from '../types/notch';

export function useAudioAnalyser() {
  const [isListening, setIsListening] = useState(false);
  const [hasPermission, setHasPermission] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafIdRef = useRef<number | null>(null);

  const [audioData, setAudioData] = useState<AudioVisualizerData>({
    volume: 0,
    peaks: [0, 0, 0, 0, 0],
    waveform: new Array(32).fill(0),
  });

  const stopListening = useCallback(() => {
    if (rafIdRef.current) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }

    setIsListening(false);
    setAudioData({
      volume: 0,
      peaks: [0, 0, 0, 0, 0],
      waveform: new Array(32).fill(0),
    });
  }, []);

  const startListening = useCallback(async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      streamRef.current = stream;
      setHasPermission(true);

      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const audioCtx = new AudioContextClass();
      audioContextRef.current = audioCtx;

      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.8;
      analyserRef.current = analyser;

      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      setIsListening(true);

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const updateLoop = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);

        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
          sum += dataArray[i];
        }
        const avg = sum / bufferLength;
        const normalizedVol = Math.min(1, avg / 128);

        // Extract 5 representative band peaks
        const step = Math.floor(bufferLength / 5);
        const peaks = [0, 1, 2, 3, 4].map((i) => {
          const val = dataArray[i * step] || 0;
          return val / 255;
        });

        // Downsample to 32 points for waveform
        const waveform = Array.from(dataArray.slice(0, 32)).map((v) => v / 255);

        setAudioData({
          volume: normalizedVol,
          peaks,
          waveform,
        });

        rafIdRef.current = requestAnimationFrame(updateLoop);
      };

      rafIdRef.current = requestAnimationFrame(updateLoop);
    } catch (err: unknown) {
      setHasPermission(false);
      const message = err instanceof Error ? err.message : 'Microphone access denied or unavailable';
      setError(message);
      setIsListening(false);
    }
  }, []);

  useEffect(() => {
    return () => {
      stopListening();
    };
  }, [stopListening]);

  return {
    isListening,
    hasPermission,
    error,
    audioData,
    startListening,
    stopListening,
  };
}
