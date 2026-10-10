import asyncio

from app.providers.vision.base import VisionProvider, VisionSample


class OpenCVCameraProvider(VisionProvider):
    """Optional camera/face provider; importing OpenCV is deferred until use."""

    name = "opencv-haar"

    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._cv2 = None
        self._capture = None
        self._classifier = None

    async def availability(self) -> tuple[bool, str]:
        try:
            import cv2
        except ImportError:
            return False, "opencv-python is not installed"
        self._cv2 = cv2
        return True, "OpenCV available; camera is not opened until initialization"

    async def initialize(self) -> None:
        available, reason = await self.availability()
        if not available:
            raise RuntimeError(reason)
        self._capture = await asyncio.to_thread(self._cv2.VideoCapture, self.camera_index)
        if self._capture is None or not self._capture.isOpened():
            if self._capture is not None:
                self._capture.release()
            self._capture = None
            raise RuntimeError(f"camera {self.camera_index} could not be opened")
        cascade_path = self._cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._classifier = self._cv2.CascadeClassifier(cascade_path)
        if self._classifier.empty():
            await self.shutdown()
            raise RuntimeError("OpenCV face detector could not be loaded")

    def _read_sample(self) -> VisionSample:
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise RuntimeError("camera returned an invalid frame")
        gray = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2GRAY)
        faces = self._classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) == 0:
            return VisionSample(False, None)
        x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
        frame_height, frame_width = gray.shape[:2]
        return VisionSample(True, 0.9, (x + width / 2) / frame_width, (y + height / 2) / frame_height)

    async def sample(self) -> VisionSample:
        if self._capture is None:
            raise RuntimeError("camera is not initialized")
        return await asyncio.to_thread(self._read_sample)

    async def shutdown(self) -> None:
        if self._capture is not None:
            await asyncio.to_thread(self._capture.release)
        self._capture = None
        self._classifier = None

