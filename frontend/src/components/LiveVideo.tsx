import { useState } from "react";

type Props = {
  streamUrl: string;
  cameraId: string;
  personName?: string | null;
  confidence?: number | null;
};

export default function LiveVideo({ streamUrl, cameraId, personName, confidence }: Props) {
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = () => {
    setFailed(false);
    setReloadKey((k) => k + 1);
  };

  return (
    <section className="live-video">
      <div className="live-video-head">
        <span className="live-badge">LIVE</span>
        <span className="live-camera">{cameraId}</span>
        <span className="live-person">
          {personName ? personName : "Unknown"}
          {typeof confidence === "number" ? (
            <span className="live-confidence">{Math.round(confidence * 100)}%</span>
          ) : null}
        </span>
        <button type="button" onClick={reload} className="live-reload">
          Reload stream
        </button>
      </div>
      {failed ? (
        <div className="live-offline">
          <p>Video stream is not available.</p>
          <p>Check that the vision pipeline is running and that the video port is reachable, then press Reload stream.</p>
        </div>
      ) : (
        <img
          key={reloadKey}
          src={streamUrl}
          alt={`Live video from ${cameraId}`}
          className="live-frame"
          onError={() => setFailed(true)}
        />
      )}
      <p className="live-note">
        Live video from the camera, with YOLO boxes drawn by the pipeline. Event data (names, confidence, positions) comes from the same feed.
      </p>
    </section>
  );
}
