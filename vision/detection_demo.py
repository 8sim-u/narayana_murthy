
from ultralytics import YOLO
import cv2


def main():
    # YOLOv8 nano model; downloads weights on first run
    model = YOLO("yolov8n.pt")

    # Open the default webcam
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("Error: Could not open webcam.")
        return

    print("WAMSS person detection started. Press Q to quit.")

    try:
        while True:
            success, frame = camera.read()

            if not success:
                print("Could not read camera frame.")
                break

            # COCO class 0 = person
            results = model.predict(
                source=frame,
                classes=[0],
                conf=0.45,
                verbose=False
            )

            annotated_frame = results[0].plot()
            cv2.imshow("WAMSS - Person Detection", annotated_frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
