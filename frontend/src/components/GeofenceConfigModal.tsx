import { useEffect, useState } from 'react';
import type { GeofenceZoneCreate, ZoneType } from '../types';
import { Field } from './ui';

interface GeofenceConfigModalProps {
  isOpen: boolean;
  polygon: [number, number][];
  officeId?: string;
  cameras: string[];
  onSave: (zone: GeofenceZoneCreate) => Promise<void>;
  onClose: () => void;
}

const DEFAULT_COLORS: Record<ZoneType, string> = {
  restricted: '#ef4444',
  workstation: '#3b82f6',
  entry_exit: '#10b981',
  custom: '#8b5cf6',
};

const COLOR_PRESETS = [
  '#ef4444', // Red
  '#3b82f6', // Blue
  '#10b981', // Green
  '#8b5cf6', // Purple
  '#f59e0b', // Amber
  '#06b6d4', // Cyan
  '#ec4899', // Pink
];

export default function GeofenceConfigModal({
  isOpen,
  polygon,
  officeId,
  cameras,
  onSave,
  onClose,
}: GeofenceConfigModalProps) {
  const [name, setName] = useState('');
  const [zoneType, setZoneType] = useState<ZoneType>('restricted');
  const [color, setColor] = useState(DEFAULT_COLORS.restricted);
  const [cameraId, setCameraId] = useState('');
  const [alertOnEnter, setAlertOnEnter] = useState(true);
  const [alertOnExit, setAlertOnExit] = useState(false);
  const [dwellThreshold, setDwellThreshold] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync color default when zone type changes
  const handleTypeChange = (newType: ZoneType) => {
    setZoneType(newType);
    setColor(DEFAULT_COLORS[newType]);
  };

  useEffect(() => {
    if (isOpen) {
      setName('');
      setZoneType('restricted');
      setColor(DEFAULT_COLORS.restricted);
      setCameraId('');
      setAlertOnEnter(true);
      setAlertOnExit(false);
      setDwellThreshold('');
      setSaving(false);
      setError(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError('Zone name cannot be empty');
      return;
    }
    if (polygon.length < 3) {
      setError('Polygon must have at least 3 vertices');
      return;
    }

    let dwellSecs: number | null = null;
    if (dwellThreshold.trim()) {
      const parsed = parseInt(dwellThreshold.trim(), 10);
      if (isNaN(parsed) || parsed <= 0) {
        setError('Dwell threshold must be a positive integer in seconds');
        return;
      }
      dwellSecs = parsed;
    }

    setError(null);
    setSaving(true);
    try {
      await onSave({
        name: trimmedName,
        office_id: officeId?.trim() || 'hq',
        camera_id: cameraId.trim() || null,
        zone_type: zoneType,
        polygon,
        color: color.trim() || DEFAULT_COLORS[zoneType],
        alert_on_enter: alertOnEnter,
        alert_on_exit: alertOnExit,
        dwell_threshold_seconds: dwellSecs,
        is_active: true,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save zone');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modal-zone-title">
      <div className="modal-sheet">
        <h2 id="modal-zone-title" className="modal-title">Configure Geofence Zone</h2>
        <p className="modal-sub">
          Define surveillance parameters and alert triggers for the {polygon.length}-point drawn boundary.
        </p>

        {error && (
          <div className="notice notice-error" role="alert" style={{ marginBottom: 16 }}>
            <p>{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div style={{ display: 'grid', gap: 14 }}>
            <Field label="Zone Name *">
              <input
                className="input"
                type="text"
                required
                placeholder="e.g. Server Rack A, Executive Suite, Exit corridor"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                autoComplete="off"
              />
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Field label="Zone Category">
                <select
                  className="select"
                  value={zoneType}
                  onChange={(e) => handleTypeChange(e.target.value as ZoneType)}
                >
                  <option value="restricted">Restricted (High Security)</option>
                  <option value="workstation">Workstation (Productivity)</option>
                  <option value="entry_exit">Entry / Exit</option>
                  <option value="custom">Custom Area</option>
                </select>
              </Field>

              <Field label="Camera Scope">
                <select
                  className="select"
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                >
                  <option value="">All cameras on floor</option>
                  {cameras.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </Field>
            </div>

            <Field label="Display Color">
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <input
                  className="input"
                  type="text"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  style={{ width: 110, fontFamily: 'var(--font-mono)' }}
                  placeholder="#ef4444"
                />
                <div className="color-presets">
                  {COLOR_PRESETS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className={`color-chip-btn ${color.toLowerCase() === p ? 'active' : ''}`}
                      style={{ background: p }}
                      onClick={() => setColor(p)}
                      title={`Select ${p}`}
                    />
                  ))}
                </div>
              </div>
            </Field>

            <Field label="Dwell Trigger Threshold (Optional)">
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input
                  className="input"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="e.g. 300 for 5 minutes"
                  value={dwellThreshold}
                  onChange={(e) => setDwellThreshold(e.target.value)}
                  style={{ width: 180, fontFamily: 'var(--font-mono)' }}
                />
                <span style={{ fontSize: 13, color: 'var(--text-2)' }}>
                  seconds of continuous stay before alert
                </span>
              </div>
            </Field>

            <div style={{ display: 'grid', gap: 8, padding: '10px 0' }}>
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer', fontSize: 13.5 }}>
                <input
                  type="checkbox"
                  checked={alertOnEnter}
                  onChange={(e) => setAlertOnEnter(e.target.checked)}
                />
                <span><strong>Alert on Zone Entry</strong> (dispatch security alert when a person enters)</span>
              </label>

              <label style={{ display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer', fontSize: 13.5 }}>
                <input
                  type="checkbox"
                  checked={alertOnExit}
                  onChange={(e) => setAlertOnExit(e.target.checked)}
                />
                <span><strong>Alert on Zone Exit</strong> (dispatch security alert when a person departs)</span>
              </label>
            </div>

            <div style={{ background: 'var(--paper-2)', padding: '10px 14px', borderRadius: 8, fontSize: 12 }}>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>
                Office: {officeId || 'hq'} &middot; Vertices: {polygon.length} normalized coordinates
              </span>
            </div>
          </div>

          <div className="modal-actions">
            <button
              type="button"
              className="btn"
              onClick={onClose}
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={saving}
            >
              {saving ? 'Creating Zone…' : 'Save Zone'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
