import { useState } from 'react';
import { sendEvent } from '../api';
import type { EventResponse } from '../types';
import { Chip, Field, SectionHead, Reveal } from './ui';

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
    <Reveal>
      <section className="panel" aria-label="Send test event">
        <SectionHead
          title="Signal Injector"
          meta="POST /event"
        />
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <Field label="Camera ID">
              <input className="input" value={cameraId} onChange={(e) => setCameraId(e.target.value)} required autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Track ID">
              <input
                className="input"
                value={trackId}
                onChange={(e) => setTrackId(e.target.value)}
                placeholder="auto-generated if empty"
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
            <Field label="Objects">
              <input className="input" value={objects} onChange={(e) => setObjects(e.target.value)} required placeholder="person, bag" autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Confidence">
              <input
                className="input"
                value={confidence}
                onChange={(e) => setConfidence(e.target.value)}
                inputMode="decimal"
                required
                style={{ width: 110 }}
              />
            </Field>
            <Field label="Zone">
              <select className="select" value={zone} onChange={(e) => setZone(e.target.value)}>
                {ZONES.map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
              </select>
            </Field>
            <button type="submit" className="btn btn-primary" disabled={sending}>
              {sending ? 'Sending…' : 'Send Event'}
            </button>
          </div>
        </form>
        {error && (
          <div className="notice notice-error rise" role="alert" style={{ marginTop: 14 }}>
            <p>{error}</p>
          </div>
        )}
        {result && (
          <div className={`notice ${isAlertResult(result) ? 'notice-warn' : 'notice-info'} rise`} style={{ marginTop: 14 }} role="status">
            <p>
              <strong>Decision: {result.decision}</strong>
              {' '}Camera {result.camera_id}. Alerts: {result.alerts.length > 0 ? result.alerts.join(', ') : 'none'}.
            </p>
            <Chip tone={isAlertResult(result) ? 'warn' : 'ok'}>
              {isAlertResult(result) ? 'REVIEW' : 'CLEAR'}
            </Chip>
          </div>
        )}
      </section>
    </Reveal>
  );
}
