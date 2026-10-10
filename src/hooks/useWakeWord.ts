import { useState, useEffect, useRef, useCallback } from 'react';

export type WakeWordStatus =
  | 'initializing'
  | 'listening'
  | 'detected'
  | 'unavailable'
  | 'permission_denied'
  | 'disabled';

export interface UseWakeWordReturn {
  status: WakeWordStatus;
  isListening: boolean;
  wakeWordDetected: boolean;
  startWakeWord: () => void;
  stopWakeWord: () => void;
}

export function useWakeWord(onWakeWordDetected: () => void): UseWakeWordReturn {
  const [status, setStatus] = useState<WakeWordStatus>('initializing');
  const [isListening, setIsListening] = useState<boolean>(false);
  const [wakeWordDetected, setWakeWordDetected] = useState<boolean>(false);

  const recognitionRef = useRef<unknown>(null);
  const isStoppingRef = useRef<boolean>(false);
  const restartTimerRef = useRef<NodeJS.Timeout | null>(null);

  const stopWakeWord = useCallback(() => {
    isStoppingRef.current = true;
    if (restartTimerRef.current) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }

    if (recognitionRef.current) {
      try {
        const stop = (recognitionRef.current as { stop: () => void }).stop;
        stop.call(recognitionRef.current);
      } catch {
        // Ignore
      }
      recognitionRef.current = null;
    }

    setIsListening(false);
    setStatus('disabled');
  }, []);

  const startWakeWord = useCallback(() => {
    isStoppingRef.current = false;
    const SpeechRecognition =
      (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).SpeechRecognition ||
      (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      // Honestly distinguish when local/browser continuous speech recognition engine is not available
      setStatus('unavailable');
      setIsListening(false);
      return;
    }

    try {
      type SpeechRecInstance = {
        continuous: boolean;
        interimResults: boolean;
        lang: string;
        onstart: () => void;
        onresult: (e: { results: Array<Array<{ transcript: string }>> }) => void;
        onerror: (e: { error: string }) => void;
        onend: () => void;
        start: () => void;
        stop: () => void;
      };

      const RecognitionClass = SpeechRecognition as new () => SpeechRecInstance;
      const rec = new RecognitionClass();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = 'en-US';

      rec.onstart = () => {
        setIsListening(true);
        setStatus('listening');
      };

      rec.onresult = (event) => {
        const results = event.results;
        for (let i = 0; i < results.length; i++) {
          const transcript = results[i][0]?.transcript?.toLowerCase() || '';
          if (transcript.includes('hey handsfree') || transcript.includes('handsfree')) {
            setWakeWordDetected(true);
            onWakeWordDetected();
            setTimeout(() => setWakeWordDetected(false), 2000);
            break;
          }
        }
      };

      rec.onerror = (event) => {
        if (event.error === 'not-allowed') {
          setStatus('permission_denied');
        } else {
          // If network or speech service is unavailable, note status honestly
          setStatus('unavailable');
        }
      };

      rec.onend = () => {
        if (!isStoppingRef.current) {
          // Continuous restart for wake-word monitoring
          restartTimerRef.current = setTimeout(() => {
            if (!isStoppingRef.current) {
              try {
                rec.start();
              } catch {
                setStatus('unavailable');
              }
            }
          }, 800);
        } else {
          setIsListening(false);
          setStatus('disabled');
        }
      };

      rec.start();
      recognitionRef.current = rec;
    } catch {
      setStatus('unavailable');
      setIsListening(false);
    }
  }, [onWakeWordDetected]);

  useEffect(() => {
    startWakeWord();

    return () => {
      stopWakeWord();
    };
  }, [startWakeWord, stopWakeWord]);

  return {
    status,
    isListening,
    wakeWordDetected,
    startWakeWord,
    stopWakeWord,
  };
}
