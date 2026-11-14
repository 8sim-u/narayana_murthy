import { useState } from 'react';
import type { GeofenceZone, ZoneOccupancy, ZoneType } from '../types';
import { Chip, Dot, SectionHead } from './ui';
import { Trash, ShieldWarning } from '@phosphor-icons/react';

interface GeofenceZoneListProps {
  zones: GeofenceZone[];
  occupancies: Record<number, ZoneOccupancy>;
  onToggleActive: (zone: GeofenceZone) => Promise<void>;
  onDeleteZone: (zoneId: number) => Promise<void>;
}

function zoneTypeTone(type: ZoneType | string): 'bad' | 'signal' | 'ok' | 'iris' {
  switch (type) {
    case 'restricted':
      return 'bad';
    case 'workstation':
      return 'signal';
    case 'entry_exit':
      return 'ok';
    default:
      return 'iris';
  }
}

export default function GeofenceZoneList({
  zones,
  occupancies,
  onToggleActive,
  onDeleteZone,
}: GeofenceZoneListProps) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const handleToggle = async (zone: GeofenceZone) => {
    setBusyId(zone.id);
    try {
      await onToggleActive(zone);
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (zoneId: number) => {
    setBusyId(zoneId);
    try {
      await onDeleteZone(zoneId);
      setConfirmDeleteId(null);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="panel" aria-label="Geofence zones registry">
      <SectionHead
        title="Geofence Zones"
        meta={`${zones.length} zone${zones.length === 1 ? '' : 's'} configured`}
      />

      {zones.length === 0 ? (
        <p style={{ color: 'var(--text-2)', fontSize: 13.5, margin: '10px 0 0' }}>
          No geofence zones defined yet. Click <strong>Draw Geofence Zone</strong> on the room map above to create one.
        </p>
      ) : (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th style={{ width: 36 }}></th>
                <th>Zone Name</th>
                <th>Category</th>
                <th>Camera Scope</th>
                <th>Alert Rules</th>
                <th>Live Occupancy</th>
                <th>Status</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {zones.map((zone) => {
                const occ = occupancies[zone.id];
                const count = occ ? occ.current_count : 0;
                const alertsCount = occ ? occ.recent_alerts_count : 0;
                const isRestrictedBreach = zone.zone_type === 'restricted' && count > 0;
                const isBusy = busyId === zone.id;

                const rules: string[] = [];
                if (zone.alert_on_enter) rules.push('Enter');
                if (zone.alert_on_exit) rules.push('Exit');
                if (zone.dwell_threshold_seconds) rules.push(`Dwell > ${zone.dwell_threshold_seconds}s`);

                return (
                  <tr key={zone.id} style={{ opacity: zone.is_active ? 1 : 0.6 }}>
                    <td>
                      <span
                        style={{
                          display: 'inline-block',
                          width: 14,
                          height: 14,
                          borderRadius: 3,
                          background: zone.color,
                          verticalAlign: 'middle',
                          boxShadow: '0 0 0 1px rgba(0,0,0,0.1)',
                        }}
                      />
                    </td>
                    <td>
                      <strong style={{ display: 'block', fontSize: 13.5 }}>{zone.name}</strong>
                      <span style={{ fontSize: 11.5, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>
                        {zone.polygon.length} vertices &middot; {zone.office_id}
                      </span>
                    </td>
                    <td>
                      <Chip tone={zoneTypeTone(zone.zone_type)}>
                        {zone.zone_type.toUpperCase()}
                      </Chip>
                    </td>
                    <td>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>
                        {zone.camera_id || 'All cameras'}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: 12.5, color: 'var(--text-2)' }}>
                        {rules.join(', ') || 'Monitoring only'}
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <span
                          style={{
                            fontFamily: 'var(--font-mono)',
                            fontSize: 13,
                            fontWeight: 600,
                            color: count > 0 ? (isRestrictedBreach ? 'var(--bad)' : 'var(--ink)') : 'var(--text-3)',
                          }}
                        >
                          {count} {count === 1 ? 'person' : 'people'}
                        </span>

                        {isRestrictedBreach && (
                          <span
                            className="chip chip-bad"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11 }}
                          >
                            <ShieldWarning size={13} weight="bold" /> BREACH
                          </span>
                        )}

                        {alertsCount > 0 && (
                          <span className="chip chip-warn" style={{ fontSize: 11 }}>
                            {alertsCount} alert{alertsCount === 1 ? '' : 's'} (24h)
                          </span>
                        )}

                        {occ && occ.occupants.length > 0 && (
                          <span style={{ fontSize: 11.5, color: 'var(--text-2)', width: '100%' }}>
                            {occ.occupants.map((o) => o.identity_name || o.person_label).join(', ')}
                          </span>
                        )}
                      </div>
                    </td>
                    <td>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <Dot tone={zone.is_active ? 'ok' : 'idle'} />
                        <span style={{ fontSize: 12.5 }}>{zone.is_active ? 'Active' : 'Paused'}</span>
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <div style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 12, padding: '4px 8px' }}
                          onClick={() => handleToggle(zone)}
                          disabled={isBusy}
                        >
                          {zone.is_active ? 'Pause' : 'Activate'}
                        </button>

                        {confirmDeleteId === zone.id ? (
                          <div style={{ display: 'inline-flex', gap: 4 }}>
                            <button
                              type="button"
                              className="btn btn-primary"
                              style={{
                                fontSize: 11,
                                padding: '4px 6px',
                                background: 'var(--bad)',
                                borderColor: 'var(--bad)',
                              }}
                              onClick={() => handleDelete(zone.id)}
                              disabled={isBusy}
                            >
                              Confirm
                            </button>
                            <button
                              type="button"
                              className="btn"
                              style={{ fontSize: 11, padding: '4px 6px' }}
                              onClick={() => setConfirmDeleteId(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="btn"
                            style={{ fontSize: 12, padding: '4px 8px', color: 'var(--bad)' }}
                            onClick={() => setConfirmDeleteId(zone.id)}
                            disabled={isBusy}
                            title="Delete zone"
                            aria-label={`Delete zone ${zone.name}`}
                          >
                            <Trash size={14} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
