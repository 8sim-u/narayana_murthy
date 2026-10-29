import { useCallback, useEffect, useState } from "react";

const API_URL = (import.meta as any).env?.VITE_API_URL || "http://localhost:8000";

type PresenceState =
  | "PRESENT_AND_ACTIVE"
  | "PRESENT_BUT_IDLE"
  | "ACTIVE_BUT_ABSENT"
  | "ABSENT";

interface PresenceItem {
  machine_id: string;
  zone: string;
  state: PresenceState;
  anomaly: boolean;
  person_present: boolean;
  active: boolean;
  last_vision_seen: string | null;
  last_activity_seen: string | null;
  employee_id: string | null;
  window_sec: number;
}

const REFRESH_MS = 10000;

const STATE_COLORS: Record<PresenceState, string> = {
  PRESENT_AND_ACTIVE: "#2e9e44",
  PRESENT_BUT_IDLE: "#d97706",
  ACTIVE_BUT_ABSENT: "#dc2626",
  ABSENT: "#9ca3af",
};

const STATE_LABELS: Record<PresenceState, string> = {
  PRESENT_AND_ACTIVE: "Present and active",
  PRESENT_BUT_IDLE: "Present but idle",
  ACTIVE_BUT_ABSENT: "Active but absent (anomaly)",
  ABSENT: "Absent",
};

const STATE_DESCRIPTIONS: Record<PresenceState, string> = {
  PRESENT_AND_ACTIVE: "Person seen in the camera zone and the workstation is in use.",
  PRESENT_BUT_IDLE: "Person seen in the camera zone but the workstation shows no input.",
  ACTIVE_BUT_ABSENT:
    "Workstation shows input but nobody is seen in the camera zone. Possible unattended remote session.",
  ABSENT: "Nobody seen in the camera zone and no workstation activity.",
};

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleString();
}

export default function PresenceView({ officeId = "" }: { officeId?: string }) {
  const [items, setItems] = useState<PresenceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = officeId ? `?office_id=${encodeURIComponent(officeId)}` : "";
      const res = await fetch(`${API_URL}/presence${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: PresenceItem[] = await res.json();
      setItems(data);
      setError(null);
    } catch (e: any) {
      setError(e?.message || "Failed to load presence data");
    } finally {
      setLoading(false);
    }
  }, [officeId]);

  useEffect(() => {
    void load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const anomalies = items.filter((i) => i.anomaly);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif" }}>
      <h2>Camera and Computer Presence</h2>
      <p style={{ color: "#555", fontSize: 13, maxWidth: 720 }}>
        Each card combines the latest person detection in the workstation camera zone with
        recent workstation input (keystrokes, mouse, active app). Cards refresh every{" "}
        {REFRESH_MS / 1000} seconds.
      </p>
      <p style={{ color: "#555", fontSize: 13, maxWidth: 720 }}>
        Zone to machine mapping comes from the <code>WAMSS_ZONE_MAP</code> environment
        variable on the backend (JSON, for example{" "}
        <code>{'{"RESTRICTED":"MACHINE_01"}'}</code>). Default zone is{" "}
        <code>RESTRICTED</code> mapped to <code>MACHINE_01</code>. The lookback window is
        set by <code>WAMSS_PRESENCE_WINDOW_SEC</code> (default 300 seconds).
      </p>

      {error && (
        <div style={{ background: "#fee2e2", padding: 12, borderRadius: 6, marginBottom: 16 }}>
          Error loading presence: {error}
        </div>
      )}

      {anomalies.length > 0 && (
        <div
          style={{
            background: "#fef2f2",
            border: "1px solid #dc2626",
            padding: 12,
            borderRadius: 6,
            marginBottom: 16,
          }}
        >
          <strong style={{ color: "#991b1b" }}>Anomaly:</strong> {anomalies.length}{" "}
          workstation(s) active with nobody in the camera zone:{" "}
          {anomalies.map((a) => a.machine_id).join(", ")}
        </div>
      )}

      {loading && <p>Loading...</p>}

      <div className="presence-grid">
        {items.map((item) => {
          const color = STATE_COLORS[item.state] || "#9ca3af";
          return (
            <div
              key={item.machine_id}
              className="presence-card"
              style={{ borderColor: color }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span
                  style={{
                    width: 14,
                    height: 14,
                    borderRadius: "50%",
                    background: color,
                    display: "inline-block",
                  }}
                />
                <strong>{item.machine_id}</strong>
                <span style={{ color: "#666", fontSize: 12 }}>zone {item.zone}</span>
              </div>
              <div style={{ fontWeight: 600, color, marginBottom: 4 }}>
                {STATE_LABELS[item.state] || item.state}
              </div>
              <p style={{ fontSize: 12, color: "#555", margin: "0 0 8px" }}>
                {STATE_DESCRIPTIONS[item.state] || ""}
              </p>
              <dl style={{ fontSize: 12, color: "#444", margin: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <dt>Person present</dt>
                  <dd style={{ margin: 0 }}>{item.person_present ? "yes" : "no"}</dd>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <dt>Workstation active</dt>
                  <dd style={{ margin: 0 }}>{item.active ? "yes" : "no"}</dd>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <dt>Employee</dt>
                  <dd style={{ margin: 0 }}>{item.employee_id || "-"}</dd>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <dt>Last seen (camera)</dt>
                  <dd style={{ margin: 0 }}>{formatTime(item.last_vision_seen)}</dd>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <dt>Last input (computer)</dt>
                  <dd style={{ margin: 0 }}>{formatTime(item.last_activity_seen)}</dd>
                </div>
              </dl>
            </div>
          );
        })}
        {items.length === 0 && !loading && (
          <span style={{ color: "#888" }}>No workstations mapped yet.</span>
        )}
      </div>
    </div>
  );
}
