import { useState, useEffect, useRef, useCallback } from 'react';

export type VoiceCommandStatus =
  | 'idle'
  | 'wake_detected'
  | 'listening_for_command'
  | 'recognizing'
  | 'parsing_intent'
  | 'executing'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'unavailable';

export interface VoiceCommandResult {
  transcript: string;
  intent: string;
  action?: string;
  success: boolean;
  message: string;
}

export interface UseVoiceCommandsReturn {
  status: VoiceCommandStatus;
  lastResult: VoiceCommandResult | null;
  lastTranscript: string | null;
  isListening: boolean;
  startListening: () => void;
  stopListening: () => void;
}

interface CommandIntent {
  intent: string;
  action: string;
  params?: Record<string, string>;
}

// Strict-mode-safe singleton guard: prevent double-mount from starting two sessions
let _activeSession = false;

// Supported site allowlist — only hardcoded, validated URLs
const SITE_ALLOWLIST: Record<string, string> = {
  'youtube':  'https://www.youtube.com',
  'google':   'https://www.google.com',
  'github':   'https://www.github.com',
  'gmail':    'https://mail.google.com',
  'twitter':  'https://www.twitter.com',
  'reddit':   'https://www.reddit.com',
  'wikipedia':'https://www.wikipedia.org',
};

export function useVoiceCommands(onWakeWord?: () => void): UseVoiceCommandsReturn {
  const [status, setStatus]           = useState<VoiceCommandStatus>('idle');
  const [lastResult, setLastResult]   = useState<VoiceCommandResult | null>(null);
  const [lastTranscript, setLastTranscript] = useState<string | null>(null);
  const [isListening, setIsListening] = useState<boolean>(false);

  // Use a ref for transcript so async executeIntent always has the current value
  const lastTranscriptRef = useRef<string | null>(null);

  const recognitionRef      = useRef<unknown>(null);
  const isStoppingRef       = useRef<boolean>(false);
  const restartTimerRef     = useRef<NodeJS.Timeout | null>(null);
  const commandCaptureModeRef = useRef<boolean>(false);
  const commandTimeoutRef   = useRef<NodeJS.Timeout | null>(null);

  // ── Intent parser ──────────────────────────────────────────────────────────
  const parseIntent = useCallback((transcript: string): CommandIntent | null => {
    const lower = transcript.toLowerCase().trim();

    // Website: "open <site>" or just "<site>"
    for (const [keyword, url] of Object.entries(SITE_ALLOWLIST)) {
      if (lower.includes(`open ${keyword}`) || lower === keyword) {
        return { intent: 'open_website', action: 'open_url', params: { url } };
      }
    }

    if (lower.includes('scroll up')   || lower.includes('page up'))   return { intent: 'scroll', action: 'scroll_up' };
    if (lower.includes('scroll down') || lower.includes('page down')) return { intent: 'scroll', action: 'scroll_down' };
    if (lower.includes('stop listening') || lower === 'stop')          return { intent: 'control', action: 'stop_listening' };

    return null;
  }, []);

  // ── Intent executor ────────────────────────────────────────────────────────
  const executeIntent = useCallback(async (intent: CommandIntent, capturedTranscript: string): Promise<VoiceCommandResult> => {
    const base = { transcript: capturedTranscript, intent: intent.intent, action: intent.action };

    if (intent.action === 'open_url' && intent.params?.url) {
      if (!window.electronAPI?.openUrl) {
        return { ...base, success: false, message: 'Electron IPC bridge not available' };
      }
      const result = await window.electronAPI.openUrl(intent.params.url);
      return result.success
        ? { ...base, success: true,  message: `Opened ${intent.params.url}` }
        : { ...base, success: false, message: result.error ?? 'URL open failed' };
    }

    if (intent.action === 'scroll_up' || intent.action === 'scroll_down') {
      return { ...base, success: false, message: 'Scrolling requires cursor-control integration (not yet active)' };
    }

    if (intent.action === 'stop_listening') {
      return { ...base, success: true, message: 'Stopped listening' };
    }

    return { ...base, success: false, message: 'Action not implemented' };
  }, []);

  // ── Stop ───────────────────────────────────────────────────────────────────
  const stopListening = useCallback(() => {
    isStoppingRef.current       = true;
    commandCaptureModeRef.current = false;

    if (commandTimeoutRef.current) { clearTimeout(commandTimeoutRef.current); commandTimeoutRef.current = null; }
    if (restartTimerRef.current)   { clearTimeout(restartTimerRef.current);   restartTimerRef.current   = null; }

    if (recognitionRef.current) {
      try { (recognitionRef.current as { stop(): void }).stop(); } catch { /* ignore */ }
      recognitionRef.current = null;
    }

    _activeSession = false;
    setIsListening(false);
    setStatus('idle');
  }, []);

  // ── Start ──────────────────────────────────────────────────────────────────
  const startListening = useCallback(() => {
    // Prevent duplicate sessions (Strict Mode, rapid re-renders)
    if (_activeSession) return;

    isStoppingRef.current         = false;
    commandCaptureModeRef.current = false;

    const SpeechRecognition =
      (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).SpeechRecognition ??
      (window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setStatus('unavailable');
      return;
    }

    try {
      type SpeechRecInstance = {
        continuous: boolean; interimResults: boolean; lang: string;
        onstart: () => void;
        onresult: (e: { results: Array<Array<{ transcript: string; isFinal: boolean }>> }) => void;
        onerror:  (e: { error: string }) => void;
        onend:    () => void;
        start: () => void; stop: () => void;
      };

      const rec = new (SpeechRecognition as new () => SpeechRecInstance)();
      rec.continuous     = true;
      rec.interimResults = true;
      rec.lang           = 'en-US';

      rec.onstart = () => {
        _activeSession = true;
        setIsListening(true);
        setStatus('idle');
      };

      rec.onresult = async (event) => {
        const results = event.results;
        for (let i = results.length - 1; i >= 0; i--) {
          const r          = results[i];
          const transcript = r[0]?.transcript?.toLowerCase().trim() ?? '';
          const isFinal    = r[0]?.isFinal ?? false;

          // Wake phrase
          if (!commandCaptureModeRef.current && (transcript.includes('hey handsfree') || transcript.includes('handsfree'))) {
            commandCaptureModeRef.current = true;
            lastTranscriptRef.current     = null;
            setLastTranscript(null);
            setStatus('wake_detected');
            if (onWakeWord) onWakeWord();

            if (commandTimeoutRef.current) clearTimeout(commandTimeoutRef.current);
            commandTimeoutRef.current = setTimeout(() => {
              commandCaptureModeRef.current = false;
              setStatus('idle');
            }, 10_000);
            break;
          }

          // Command capture
          if (commandCaptureModeRef.current && transcript && !transcript.includes('hey handsfree') && transcript !== 'handsfree') {
            if (!isFinal) {
              lastTranscriptRef.current = transcript;
              setLastTranscript(transcript);
              setStatus('listening_for_command');
              break;
            }

            // Final result — commit
            commandCaptureModeRef.current = false;
            if (commandTimeoutRef.current) { clearTimeout(commandTimeoutRef.current); commandTimeoutRef.current = null; }

            const captured = transcript;
            lastTranscriptRef.current = captured;
            setLastTranscript(captured);
            setStatus('recognizing');

            await new Promise(r => setTimeout(r, 0)); // let React flush status update

            setStatus('parsing_intent');
            const intent = parseIntent(captured);

            if (!intent) {
              setLastResult({ transcript: captured, intent: 'unknown', success: false,
                message: 'Command not recognized. Try "open YouTube", "open Google", "scroll up", or "stop listening".' });
              setStatus('failed');
            } else {
              setStatus('executing');
              const result = await executeIntent(intent, captured);
              setLastResult(result);
              setStatus(result.success ? 'completed' : 'failed');

              if (intent.action === 'stop_listening') { stopListening(); return; }
            }

            // Auto-clear result display after 4 s
            setTimeout(() => {
              if (!isStoppingRef.current) setStatus('idle');
            }, 4_000);
            break;
          }
        }
      };

      rec.onerror = (event) => {
        if (event.error === 'not-allowed') {
          setStatus('unavailable');
          setIsListening(false);
          isStoppingRef.current = true;
          _activeSession = false;
        } else if (event.error !== 'no-speech' && event.error !== 'aborted') {
          // non-fatal: will restart on onend
          console.warn('SpeechRecognition error:', event.error);
        }
      };

      rec.onend = () => {
        _activeSession = false;
        if (isStoppingRef.current) {
          setIsListening(false);
          setStatus('idle');
          return;
        }
        // Restart after 1 s to avoid tight loop from rapid end events
        restartTimerRef.current = setTimeout(() => {
          if (!isStoppingRef.current) {
            try {
              recognitionRef.current = null;
              startListening();
            } catch {
              setStatus('unavailable');
            }
          }
        }, 1_000);
      };

      rec.start();
      recognitionRef.current = rec;
    } catch {
      setStatus('unavailable');
      _activeSession = false;
    }
  }, [parseIntent, executeIntent, stopListening, onWakeWord]);

  // ── Cleanup on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      isStoppingRef.current = true;
      if (commandTimeoutRef.current) clearTimeout(commandTimeoutRef.current);
      if (restartTimerRef.current)   clearTimeout(restartTimerRef.current);
      if (recognitionRef.current) {
        try { (recognitionRef.current as { stop(): void }).stop(); } catch { /* ignore */ }
        recognitionRef.current = null;
      }
      _activeSession = false;
    };
  }, []);

  return { status, lastResult, lastTranscript, isListening, startListening, stopListening };
}
