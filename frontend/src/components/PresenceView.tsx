import { useCallback, useEffect, useState } from "react";
import {
  Chip, Dot, EmptyState, ErrorNotice, Reveal, SectionHead,
  SkeletonCards, Stat, formatClock,
} from "./ui";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

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

interface AttendanceItem {
  employee_id: string;
  first_seen: string | null;
  last_seen: string | null;
  present_minutes: number;
  camera_minutes: number;
  computer_minutes: number;
  sources: string[];
}

interface DailyAttendance {
  day: string;
  enabled: boolean;
  items: AttendanceItem[];
}

const REFRESH_MS = 10000;

function formatUtcClock(value: string | null): string {
  if (!value) return "-";
  const instant = new Date(value);
  return Number.isNaN(instant.getTime())
    ? value
    : instant.toLocaleString(undefined, { timeZone: "UTC" });
}

const STATE_TONE: Record<PresenceState, "ok" | "warn" | "bad" | "idle"> = {
  PRESENT_AND_ACTIVE: "ok",
  PRESENT_BUT_IDLE: "warn",
  ACTIVE_BUT_ABSENT: "bad",
  ABSENT: "idle",
};

const STATE_LABELS: Record<PresenceState, string> = {
  PRESENT_AND_ACTIVE: "Present and active",
  PRESENT_BUT_IDLE: "Present but idle",
  ACTIVE_BUT_ABSENT: "Active but absent",
  ABSENT: "Absent",
};

const STATE_DESCRIPTIONS: Record<PresenceState, string> = {
  PRESENT_AND_ACTIVE: "Person seen in the camera zone and the workstation is in use.",
  PRESENT_BUT_IDLE: "Person seen in the camera zone but the workstation shows no input.",
  ACTIVE_BUT_ABSENT:
    "Workstation shows input but nobody is seen in the camera zone. Possible unattended remote session.",
  ABSENT: "Nobody seen in the camera zone and no workstation activity.",
};

export default function PresenceView({ officeId = "" }: { officeId?: string }) {
  const [items, setItems] = useState<PresenceItem[]>([]);
  const [attendance, setAttendance] = useState<DailyAttendance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params = officeId ? `?office_id=${encodeURIComponent(officeId)}` : "";
      const [res, attendanceRes] = await Promise.all([
        fetch(`${API_URL}/presence${params}`),
        fetch(`${API_URL}/attendance/daily`),
      ]);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (!attendanceRes.ok) throw new Error(`attendance: HTTP ${attendanceRes.status}`);
      const data: PresenceItem[] = await res.json();
      setItems(data);
      setAttendance(await attendanceRes.json());
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load presence data");
    } finally {
      setLoading(false);
    }
  }, [officeId]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => { void load(); }, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const anomalies = items.filter((i) => i.anomaly);
  const present = items.filter((i) => i.state === "PRESENT_AND_ACTIVE").length;

  return (
    <div className="rise">
      {error && (
        <ErrorNotice
          message={`Could not load presence data: ${error}`}
          onRetry={() => { void load(); }}
        />
      )}

      {anomalies.length > 0 && (
        <div className="notice notice-error rise" role="alert">
          <p>
            <strong>Anomaly:</strong> {anomalies.length} workstation(s) active with nobody in the
            camera zone: {anomalies.map((a) => a.machine_id).join(", ")}
          </p>
          <Chip tone="bad">ANOMALY</Chip>
        </div>
      )}

      <Reveal>
        <section className="panel" aria-label="Presence overview">
          <SectionHead title="Floor State" meta={`refresh ${REFRESH_MS / 1000}s`} />
          <div className="grid-4">
            <Stat label="Stations" value={items.length} mono />
            <Stat label="Present and active" value={present} mono tone="var(--ok)" />
            <Stat label="Anomalies" value={anomalies.length} mono tone={anomalies.length > 0 ? "var(--bad)" : "var(--text-2)"} />
            <Stat label="Window" value={`${items[0]?.window_sec ?? 300}s`} mono />
          </div>
          <p style={{ color: "var(--text-2)", fontSize: 13, margin: "14px 0 0", maxWidth: "80ch" }}>
            Each station combines the latest person detection in the workstation camera zone with
            recent workstation input. Zone to machine mapping comes from the{" "}
            <code>WAMSS_ZONE_MAP</code> backend setting; the lookback window from{" "}
            <code>WAMSS_PRESENCE_WINDOW_SEC</code> (default 300 seconds).
          </p>
        </section>
      </Reveal>

      <Reveal>
        <section className="panel" aria-label="Daily attendance">
          <SectionHead
            title="Daily Attendance"
            meta={attendance?.enabled ? `${attendance.day} · UTC` : "opt-in"}
          />
          {!attendance?.enabled ? (
            <p style={{ color: "var(--text-2)", fontSize: 13, margin: 0, maxWidth: "80ch" }}>
              Daily attendance is disabled by default. Enable it with <code>WAMSS_ATTENDANCE_ENABLED=1</code> after providing employee notice and configuring camera identity links where needed.
            </p>
          ) : attendance.items.length === 0 ? (
            <EmptyState title="No attendance sources today" body="Recognized enrolled camera sightings and non-idle workstation segments appear here with their source." />
          ) : (
            <div className="table-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Employee</th>
                    <th>First seen (UTC)</th>
                    <th>Last seen (UTC)</th>
                    <th>Present</th>
                    <th>Camera</th>
                    <th>Computer</th>
                    <th>Sources</th>
                  </tr>
                </thead>
                <tbody>
                  {attendance.items.map((row) => (
                    <tr key={row.employee_id}>
                      <td className="id">{row.employee_id}</td>
                      <td className="num">{formatUtcClock(row.first_seen)}</td>
                      <td className="num">{formatUtcClock(row.last_seen)}</td>
                      <td className="num">{row.present_minutes.toFixed(1)} min</td>
                      <td className="num">{row.camera_minutes.toFixed(1)} min</td>
                      <td className="num">{row.computer_minutes.toFixed(1)} min</td>
                      <td>{row.sources.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </Reveal>

      <section aria-label="Stations">
        {loading && items.length === 0 ? (
          <SkeletonCards count={3} />
        ) : items.length === 0 ? (
          <div className="panel">
            <EmptyState
              title="No workstations mapped"
              body="Map camera zones to machines in the backend WAMSS_ZONE_MAP setting and stations will appear here."
            />
          </div>
        ) : (
          <div className="station-grid">
            {items.map((item, i) => {
              const tone = STATE_TONE[item.state] ?? "idle";
              return (
                <article
                  key={item.machine_id}
                  className={`station-card station-${tone} rise-stagger`}
                  style={{ "--i": Math.min(i, 12) } as React.CSSProperties}
                  aria-label={`${item.machine_id}: ${STATE_LABELS[item.state] ?? item.state}`}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <Dot tone={tone} label={STATE_LABELS[item.state] ?? item.state} />
                    <strong style={{ fontFamily: "var(--font-display)", fontSize: 15 }}>{item.machine_id}</strong>
                    <span style={{ marginLeft: "auto" }}>
                      <Chip tone={tone === "ok" ? "ok" : tone === "warn" ? "warn" : tone === "bad" ? "bad" : "idle"}>
                        {STATE_LABELS[item.state] ?? item.state}
                      </Chip>
                    </span>
                  </div>
                  <p style={{ fontSize: 12.5, color: "var(--text-2)", margin: "0 0 12px" }}>
                    {STATE_DESCRIPTIONS[item.state] ?? ""}
                  </p>
                  <dl style={{ margin: 0 }}>
                    <div className="kv"><dt>Zone</dt><dd>{item.zone}</dd></div>
                    <div className="kv"><dt>Person present</dt><dd>{item.person_present ? "yes" : "no"}</dd></div>
                    <div className="kv"><dt>Workstation active</dt><dd>{item.active ? "yes" : "no"}</dd></div>
                    <div className="kv"><dt>Employee</dt><dd>{item.employee_id || "-"}</dd></div>
                    <div className="kv"><dt>Last seen (camera)</dt><dd>{formatClock(item.last_vision_seen)}</dd></div>
                    <div className="kv"><dt>Last input (computer)</dt><dd>{formatClock(item.last_activity_seen)}</dd></div>
                  </dl>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
