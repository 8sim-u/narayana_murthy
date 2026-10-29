import { useState } from 'react';
import { sendEvent } from '../api';
import type { EventResponse } from '../types';

const ZONES = ['RESTRICTED', 'ENTRY', 'WORKSPACE', 'UNKNOWN'];

function isAlertResult(result: EventResponse): boolean {
  return result.decision === 'REVIEW_REQUIRED' || result.alerts.includes('RESTRICTED_ZONE_DETECTED');
}

export default function TestEventForm({ onSent }: { onSent: () => void }) {
  const [cameraId, setCameraId] = useState('CAM_01');
  const [trackId, setTrackId] = useState('');
  const [objects, setObjects] = useState('person');
  const [confidence, setConfidence] = useState('0.90');
  const [zone, setZone] = useState('WORKSPACE');
  const [result, setResult] = useState<EventResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setResult(null);
    const conf = Number.parseFloat(confidence);
    if (Number.isNaN(conf) || conf < 0 || conf > 1) {
      setError('Confidence must be a number between 0.0 and 1.0.');
      return;
    }
    setSending(true);
    try {
      const resp = await sendEvent({
        camera_id: cameraId.trim(),
        track_id: trackId.trim() || `track-${Date.now()}`,
        objects: objects.split(',').map((o) => o.trim()).filter(Boolean),
        confidence: conf,
        zone,
      });
      setResult(resp);
      onSent();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send event');
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="panel">
      <h2>Send test event</h2>
      <form onSubmit={handleSubmit} className="test-form">
        <label>
          Camera ID
          <input value={cameraId} onChange={(e) => setCameraId(e.target.value)} required />
        </label>
        <label>
          Track ID
          <input
            value={trackId}
            onChange={(e) => setTrackId(e.target.value)}
            placeholder="auto-generated if empty"
          />
        </label>
        <label>
          Objects (comma separated)
          <input value={objects} onChange={(e) => setObjects(e.target.value)} required />
        </label>
        <label>
          Confidence (0.0 - 1.0)
          <input
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            inputMode="decimal"
            required
          />
        </label>
        <label>
          Zone
          <select value={zone} onChange={(e) => setZone(e.target.value)}>
            {ZONES.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={sending}>
          {sending ? 'Sending...' : 'Send event'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className={`result ${isAlertResult(result) ? 'alert-box' : 'ok-box'}`}>
          <strong>Decision: {result.decision}</strong>
          <div>Camera: {result.camera_id}</div>
          <div>Alerts: {result.alerts.length > 0 ? result.alerts.join(', ') : 'none'}</div>
        </div>
      )}
    </section>
  );
}
