
from ultralytics import YOLO
import cv2


def main():
    model = YOLO("yolov8n.pt")
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("Error: Could not open webcam.")
        return

    print("WAMSS tracking started. Press Q to quit.")

    try:
        while True:
            success, frame = camera.read()

            if not success:
                print("Could not read camera frame.")
                break

            # ByteTrack assigns IDs to detected people across frames.
            results = model.track(
                source=frame,
                persist=True,
                tracker="bytetrack.yaml",
                classes=[0],       # Person class only
                conf=0.45,
                verbose=False
            )

            annotated_frame = results[0].plot()
            cv2.imshow("WAMSS - Person Tracking", annotated_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
