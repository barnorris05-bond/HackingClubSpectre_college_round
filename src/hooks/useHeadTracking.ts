import { useState, useEffect, useRef, useCallback } from 'react';
import { backendApi, getVisionStreamUrl, type VisionStatusResponse } from '../services/backendApi';

export type CameraStatus =
  | 'inactive'
  | 'starting'
  | 'loading_model'
  | 'model_loaded'
  | 'active_awaiting_face'
  | 'face_detected'
  | 'tracking_active'
  | 'tracking_lost'
  | 'permission_denied'
  | 'camera_error'
  | 'model_error';

export interface UseHeadTrackingReturn {
  videoRef: React.RefObject<HTMLImageElement | null>;
  calibrated: boolean;
  errorMessage: string | null;
  previewUrl: string | null;
  status: CameraStatus;
  startCamera: () => Promise<void>;
  stopCamera: () => void;
  calibrate: () => void;
}

function cameraStatusFromBackend(snapshot: VisionStatusResponse): CameraStatus {
  if (snapshot.error_code === 'model_load_failed') return 'model_error';
  const state = snapshot.camera?.status;
  if (state === 'starting') return 'starting';
  if (state === 'running') return 'active_awaiting_face';
  if (state === 'error') return snapshot.error_code?.includes('model') ? 'model_error' : 'camera_error';
  if (state === 'unavailable') return 'camera_error';
  return 'inactive';
}

export function useHeadTracking(): UseHeadTrackingReturn {
  const [status, setStatus] = useState<CameraStatus>('inactive');
  const [calibrated, setCalibrated] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const videoRef = useRef<HTMLImageElement | null>(null);
  const lastFrameStateRef = useRef<string>('');

  const applyStatus = useCallback((snapshot: VisionStatusResponse) => {
    const nextStatus = cameraStatusFromBackend(snapshot);
    setStatus(nextStatus);
    setCalibrated(snapshot.calibration?.calibrated === true || snapshot.calibration?.state === 'ready');
    const error = snapshot.error || snapshot.camera?.detail || snapshot.unavailable_reason || null;
    setErrorMessage(error);

    const cameraRunning = snapshot.running && snapshot.camera?.status === 'running';
    if (cameraRunning) {
      setStatus(snapshot.tracking?.active ? 'tracking_active'
        : snapshot.tracking?.reason === 'tracking_lost' ? 'tracking_lost'
          : 'active_awaiting_face');
      const streamKey = `${snapshot.running}:${snapshot.camera?.status}`;
      if (lastFrameStateRef.current !== streamKey) {
        lastFrameStateRef.current = streamKey;
        setPreviewUrl(`${getVisionStreamUrl()}?started=${Date.now()}`);
      }
    } else {
      lastFrameStateRef.current = '';
      setPreviewUrl(null);
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      applyStatus(await backendApi.getVisionStatus());
    } catch (err: unknown) {
      setPreviewUrl(null);
      setStatus('camera_error');
      setErrorMessage(err instanceof Error ? err.message : 'Unable to read backend vision status');
    }
  }, [applyStatus]);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 1000);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  const startCamera = useCallback(async () => {
    setStatus('starting');
    setErrorMessage(null);
    try {
      applyStatus(await backendApi.startVision());
      // The worker opens the camera asynchronously; polling exposes its actual
      // state and startup errors without claiming it is already running.
      void refreshStatus();
    } catch (err: unknown) {
      setStatus('camera_error');
      setErrorMessage(err instanceof Error ? err.message : 'Backend vision start failed');
    }
  }, [applyStatus, refreshStatus]);

  const stopCamera = useCallback(() => {
    setPreviewUrl(null);
    void backendApi.stopVision().then(applyStatus).catch((err: unknown) => {
      setStatus('camera_error');
      setErrorMessage(err instanceof Error ? err.message : 'Backend vision stop failed');
    });
  }, [applyStatus]);

  const calibrate = useCallback(() => {
    void backendApi.calibrateVision().then(() => refreshStatus()).catch((err: unknown) => {
      setErrorMessage(err instanceof Error ? err.message : 'Backend calibration failed');
    });
  }, [refreshStatus]);

  return {
    status,
    videoRef,
    calibrated,
    errorMessage,
    previewUrl,
    startCamera,
    stopCamera,
    calibrate,
  };
}
