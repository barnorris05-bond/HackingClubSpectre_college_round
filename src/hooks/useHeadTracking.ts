import { useState, useEffect, useRef, useCallback } from 'react';
import { FaceLandmarker, FilesetResolver, FaceLandmarkerResult } from '@mediapipe/tasks-vision';

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

export interface HeadPoseData {
  pitch: number; // degrees
  yaw: number;   // degrees
  roll: number;  // degrees
  confidence: number;
}

export interface UseHeadTrackingReturn {
  status: CameraStatus;
  stream: MediaStream | null;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  headPose: HeadPoseData | null;
  calibrated: boolean;
  errorMessage: string | null;
  startCamera: () => Promise<void>;
  stopCamera: () => void;
  calibrate: () => void;
}

export function useHeadTracking(): UseHeadTrackingReturn {
  const [status, setStatus] = useState<CameraStatus>('inactive');
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [headPose, setHeadPose] = useState<HeadPoseData | null>(null);
  const [calibrated, setCalibrated] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const isStoppingRef = useRef<boolean>(false);
  const hasUserStoppedRef = useRef<boolean>(false);
  const faceLandmarkerRef = useRef<FaceLandmarker | null>(null);
  const lastDetectionTimeRef = useRef<number>(0);
  const initializationAttemptedRef = useRef<boolean>(false);
  // Track whether a startCamera call is in-progress (to guard concurrent invocations)
  const isInitializingRef = useRef<boolean>(false);

  // Calibration baseline offset
  const baselinePoseRef = useRef<{ pitch: number; yaw: number; roll: number }>({ pitch: 0, yaw: 0, roll: 0 });
  
  // Smoothing for jitter reduction
  const smoothingBufferRef = useRef<Array<{ pitch: number; yaw: number; roll: number }>>([]);
  const SMOOTHING_WINDOW = 3;


  const stopCamera = useCallback(() => {
    hasUserStoppedRef.current = true;
    isStoppingRef.current = true;
    // Allow a future explicit startCamera() call to re-initialize
    initializationAttemptedRef.current = false;

    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }

    if (stream) {
      stream.getTracks().forEach((track) => {
        track.stop();
      });
      setStream(null);
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }

    setStatus('inactive');
    setHeadPose(null);
  }, [stream]);

  const calibrate = useCallback(() => {
    if (headPose) {
      baselinePoseRef.current = {
        pitch: headPose.pitch,
        yaw: headPose.yaw,
        roll: headPose.roll,
      };
      setCalibrated(true);
    }
  }, [headPose]);

  // Calculate head pose from face landmarks using genuine MediaPipe output
  const calculateHeadPose = useCallback((result: FaceLandmarkerResult): { pitch: number; yaw: number; roll: number; confidence: number } | null => {
    if (!result.faceLandmarks || result.faceLandmarks.length === 0) {
      return null;
    }

    const landmarks = result.faceLandmarks[0];
    
    // Key facial landmarks for head pose estimation
    // MediaPipe Face Landmarker provides 478 3D landmarks
    // Key indices: nose tip (1), left eye (33), right eye (263), left mouth (61), right mouth (291)
    const noseTip = landmarks[1];
    const leftEye = landmarks[33];
    const rightEye = landmarks[263];
    const leftMouth = landmarks[61];
    const rightMouth = landmarks[291];
    const chinBottom = landmarks[152];

    // Calculate yaw (horizontal rotation) from eye positions
    const eyeMidX = (leftEye.x + rightEye.x) / 2;
    const noseXOffset = noseTip.x - eyeMidX;
    const yaw = noseXOffset * 120; // Scale to degrees, calibrated empirically

    // Calculate pitch (vertical rotation) from nose-to-chin distance and eye-to-mouth distance
    const noseChinDist = Math.abs(noseTip.y - chinBottom.y);
    const eyeMouthDist = Math.abs((leftEye.y + rightEye.y) / 2 - (leftMouth.y + rightMouth.y) / 2);
    const pitchRatio = eyeMouthDist / (noseChinDist + 0.01); // Avoid division by zero
    const pitch = (pitchRatio - 0.3) * 150; // Normalized around neutral position

    // Calculate roll (head tilt) from eye alignment
    const eyeDeltaY = leftEye.y - rightEye.y;
    const eyeDeltaX = leftEye.x - rightEye.x;
    const roll = Math.atan2(eyeDeltaY, eyeDeltaX) * (180 / Math.PI);

    // Use z-depth variance as confidence indicator (closer = higher confidence)
    // MediaPipe provides normalized depth; lower variance = more stable detection
    const avgDepth = landmarks.reduce((sum, lm) => sum + (lm.z || 0), 0) / landmarks.length;
    const depthVariance = landmarks.reduce((sum, lm) => sum + Math.abs((lm.z || 0) - avgDepth), 0) / landmarks.length;
    const confidence = Math.max(0.5, Math.min(0.98, 1.0 - depthVariance * 10));

    return { pitch, yaw, roll, confidence };
  }, []);

  // Apply smoothing to reduce jitter
  const applySmoothing = useCallback((pose: { pitch: number; yaw: number; roll: number }) => {
    smoothingBufferRef.current.push(pose);
    if (smoothingBufferRef.current.length > SMOOTHING_WINDOW) {
      smoothingBufferRef.current.shift();
    }

    const smoothed = smoothingBufferRef.current.reduce(
      (acc, p) => ({
        pitch: acc.pitch + p.pitch / smoothingBufferRef.current.length,
        yaw: acc.yaw + p.yaw / smoothingBufferRef.current.length,
        roll: acc.roll + p.roll / smoothingBufferRef.current.length,
      }),
      { pitch: 0, yaw: 0, roll: 0 }
    );

    return smoothed;
  }, []);

  const startCamera = useCallback(async () => {
    // Guard against concurrent startCamera calls (e.g. button double-click, Strict Mode)
    if (isInitializingRef.current) return;
    isInitializingRef.current = true;

    try {
      hasUserStoppedRef.current = false;
      isStoppingRef.current = false;
      setStatus('starting');
      setErrorMessage(null);

      // Check if mediaDevices API is available
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatus('camera_error');
        setErrorMessage('Camera API is not supported in this environment');
        return;
      }

      // Request camera permission and start stream
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          frameRate: { ideal: 30 },
          facingMode: 'user',
        },
        audio: false,
      });

      if (isStoppingRef.current) {
        mediaStream.getTracks().forEach((t) => t.stop());
        isInitializingRef.current = false;
        return;
      }

      setStream(mediaStream);
      setStatus('loading_model');

      if (videoRef.current) {
        videoRef.current.srcObject = mediaStream;
        await videoRef.current.play().catch(() => {});
      }

      // Initialize MediaPipe Face Landmarker
      if (!faceLandmarkerRef.current) {
        try {
          // Use local WASM files bundled with the app instead of CDN
          const vision = await FilesetResolver.forVisionTasks('/wasm');
          
          const landmarker = await FaceLandmarker.createFromOptions(vision, {
            baseOptions: {
              modelAssetPath: '/models/face_landmarker.task',
              delegate: 'GPU', // Use GPU acceleration if available
            },
            runningMode: 'VIDEO',
            numFaces: 1,
            minFaceDetectionConfidence: 0.5,
            minFacePresenceConfidence: 0.5,
            minTrackingConfidence: 0.5,
            outputFaceBlendshapes: false,
            outputFacialTransformationMatrixes: false,
          });

          faceLandmarkerRef.current = landmarker;
          setStatus('model_loaded');
        } catch (err) {
          console.error('Failed to initialize MediaPipe Face Landmarker:', err);
          setStatus('model_error');
          setErrorMessage(err instanceof Error ? `Model load failed: ${err.message}` : 'Failed to load face tracking model from local assets.');
          isInitializingRef.current = false;
          return;
        }
      } else {
        setStatus('model_loaded');
      }

      setStatus('active_awaiting_face');

      // Tracking loop
      let lastProcessTime = 0;
      const FRAME_INTERVAL = 66; // ~15 fps for performance

      const processFrame = async () => {
        if (isStoppingRef.current || !faceLandmarkerRef.current) return;

        const now = performance.now();
        
        if (now - lastProcessTime > FRAME_INTERVAL && videoRef.current && videoRef.current.readyState >= 2) {
          lastProcessTime = now;

          try {
            const result = faceLandmarkerRef.current.detectForVideo(videoRef.current, now);

            if (result.faceLandmarks && result.faceLandmarks.length > 0) {
              lastDetectionTimeRef.current = now;
              
              const rawPose = calculateHeadPose(result);
              if (rawPose) {
                // Apply smoothing
                const smoothedPose = applySmoothing({ pitch: rawPose.pitch, yaw: rawPose.yaw, roll: rawPose.roll });

                // Apply calibration offset
                const calibratedPose = {
                  pitch: Math.round((smoothedPose.pitch - baselinePoseRef.current.pitch) * 10) / 10,
                  yaw: Math.round((smoothedPose.yaw - baselinePoseRef.current.yaw) * 10) / 10,
                  roll: Math.round((smoothedPose.roll - baselinePoseRef.current.roll) * 10) / 10,
                  confidence: Math.round(rawPose.confidence * 100) / 100,
                };

                setHeadPose(calibratedPose);
                setStatus('tracking_active');
              }
            } else {
              // No face detected
              if (now - lastDetectionTimeRef.current > 500) {
                // Lost tracking for more than 500ms
                setStatus('tracking_lost');
                setHeadPose(null);
                smoothingBufferRef.current = [];
              } else {
                setStatus('active_awaiting_face');
              }
            }
          } catch (err) {
            console.warn('Frame processing error:', err);
            setStatus('active_awaiting_face');
          }
        }

        animFrameRef.current = requestAnimationFrame(processFrame);
      };

      animFrameRef.current = requestAnimationFrame(processFrame);
    } catch (err: unknown) {
      if (err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')) {
        setStatus('permission_denied');
        setErrorMessage('Camera access was denied by the user or operating system.');
      } else {
        setStatus('camera_error');
        setErrorMessage(err instanceof Error ? err.message : 'Unable to initialize webcam.');
      }
    } finally {
      isInitializingRef.current = false;
    }
  }, [calculateHeadPose, applySmoothing]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (animFrameRef.current) {
        cancelAnimationFrame(animFrameRef.current);
      }
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
      if (faceLandmarkerRef.current) {
        faceLandmarkerRef.current.close();
        faceLandmarkerRef.current = null;
      }
    };
  }, [stream]);

  // Automatic initialization on mount.
  // initializationAttemptedRef prevents Strict Mode's double-invoke from starting two streams.
  useEffect(() => {
    if (initializationAttemptedRef.current) return;
    if (hasUserStoppedRef.current) return;
    initializationAttemptedRef.current = true;
    startCamera();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally empty — we only want this on first real mount

  return {
    status,
    stream,
    videoRef,
    canvasRef,
    headPose,
    calibrated,
    errorMessage,
    startCamera,
    stopCamera,
    calibrate,
  };
}
