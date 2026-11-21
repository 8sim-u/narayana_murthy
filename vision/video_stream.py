# MJPEG video streamer for the WAMSS vision pipeline.
# Runs a small HTTP server in a background thread INSIDE the pipeline process.
# It must live here because the pipeline is the only process that can read the
# camera (Windows cameras are exclusive), so the backend cannot open it to
# stream. The pipeline feeds this server its latest annotated frame and the
# dashboard shows it as live video.
# Endpoints: GET /video (MJPEG stream), GET /snapshot.jpg (one JPEG),
# GET /health (JSON). Listens on 127.0.0.1:8002 by default.
# Stdlib only (http.server + cv2, which the pipeline already uses).

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2


class VideoStreamer:
    def __init__(self, port=8002):
        self.port = port
        self._frame_jpeg = None
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def set_frame(self, frame_bgr):
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return
        with self._lock:
            self._frame_jpeg = bytes(buf)

    def _handler_class(self):
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, "application/json", b'{"status":"online","service":"WAMSS video"}')
                    return
                if self.path == "/snapshot.jpg":
                    with streamer._lock:
                        data = streamer._frame_jpeg
                    if data is None:
                        self._send(503, "text/plain", b"no frame yet")
                    else:
                        self._send(200, "image/jpeg", data)
                    return
                if self.path == "/video":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while True:
                        with streamer._lock:
                            data = streamer._frame_jpeg
                        if data is None:
                            time.sleep(0.1)
                            continue
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(("Content-Length: %d\r\n\r\n" % len(data)).encode())
                            self.wfile.write(data)
                            self.wfile.write(b"\r\n")
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        time.sleep(0.05)
                    return
                self._send(404, "text/plain", b"not found")

        return Handler

    def start(self):
        if self._server is not None:
            return
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server = None
