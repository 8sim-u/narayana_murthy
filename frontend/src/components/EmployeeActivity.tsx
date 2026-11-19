import { useEffect, useMemo, useState } from "react";
import { ArrowClockwise } from "@phosphor-icons/react";
import ExportButton from "./ExportButton";
import {
  Chip, Dot, EmptyState, ErrorNotice, Field, Meter, Reveal,
  SectionHead, SkeletonRows, Stat, strainDot,
} from "./ui";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

// This console shows this laptop only. The filter input below defaults to
// this machine, and every list on this tab is hard-filtered to it as well,
// so no other machine can appear even if the filter input is cleared.
const THIS_MACHINE = "DESKTOP-0FLG5F5";

interface ActivityRecord {
  id: number;
  machine_id: string;
  employee_id: string;
  event_type: string;
  app_name: string;
  window_title: string;
  process_name: string;
  start_time: string;
  end_time: string;
  keystrokes: number;
  mouse_clicks: number;
  is_idle: boolean;
  strain_score: number | null;
  strain_level: string | null;
}

interface StrainSummary {
  machine_id: string;
  employee_id: string;
  strain_score: number | null;
  strain_level: string;
  last_seen: string | null;
  is_idle: boolean;
  // Not yet served by GET /activity/strain-summary; renders "-" until the backend ships it.
  ml_stress_score?: number | null;
}

interface AppUsageItem {
  employee_id: string;
  machine_id: string;
  app_name: string;
  category: "work" | "break" | "neutral";
  minutes: number;
}

interface FocusDaily {
  employee_id: string;
  date: string;
  focus_score: number;
}

const EVENT_TONE: Record<string, "signal" | "bad" | "warn" | "iris" | "idle"> = {
  activity: "signal",
  alert: "bad",
  break_suggested: "warn",
  fatigue_suspected: "iris",
};

function eventTone(type: string) {
  return EVENT_TONE[type] ?? "idle";
}

function focusColor(score: number): string {
  if (score >= 70) return "var(--ok)";
  if (score >= 40) return "var(--warn)";
  return "var(--bad)";
}

function minutesBetween(start: string, end: string): number {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Math.max(0, ms / 60000);
}

function formatClock(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}

// Human-readable duration: 83 -> "1h 23m", 45 -> "45m", 120 -> "2h", 5 -> "5m".
function formatDuration(totalMinutes: number): string {
  const m = Math.max(0, Math.round(totalMinutes));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest === 0 ? `${h}h` : `${h}h ${rest}m`;
}

// App names arrive as process names ("chrome.exe"); match them case-insensitively.
function normalizeApp(name: string): string {
  return name.trim().toLowerCase();
}

// Compact first/last seen stamp: time only on the selected day, short date
// plus time otherwise.
function formatSeen(iso: string | null, day: string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  const localDay = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (localDay === day) return time;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

export default function EmployeeActivity({ officeId = "" }: { officeId?: string }) {
  const [records, setRecords] = useState<ActivityRecord[]>([]);
  const [strain, setStrain] = useState<StrainSummary[]>([]);
  const [appUsage, setAppUsage] = useState<AppUsageItem[]>([]);
  const [focus, setFocus] = useState<FocusDaily | null>(null);
  const [employeeId, setEmployeeId] = useState("");
  const [machineId, setMachineId] = useState(THIS_MACHINE);
  const [appUsageDay, setAppUsageDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [idleThresholdMin, setIdleThresholdMin] = useState(15);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (employeeId) params.set("employee_id", employeeId);
      if (machineId) params.set("machine_id", machineId);
      if (officeId) params.set("office_id", officeId);
      params.set("limit", "500");
      const usageParams = new URLSearchParams(params);
      usageParams.set("day", appUsageDay);
      const [actRes, strainRes, usageRes] = await Promise.all([
        fetch(`${API_URL}/activity?${params.toString()}`),
        fetch(`${API_URL}/activity/strain-summary`),
        fetch(`${API_URL}/activity/app-usage?${usageParams.toString()}`),
      ]);
      if (!actRes.ok) throw new Error(`activity: HTTP ${actRes.status}`);
      if (!strainRes.ok) throw new Error(`strain-summary: HTTP ${strainRes.status}`);
      if (!usageRes.ok) throw new Error(`app-usage: HTTP ${usageRes.status}`);
      const data = (await actRes.json()) as ActivityRecord[];
      setRecords(data);
      setStrain(await strainRes.json());
      setAppUsage(await usageRes.json());
      // The focus score belongs to a person, so it is fetched for the person
      // these records belong to: the explicit filter, or the employee the
      // returned activity rows carry. No score without that person.
      const focusFor = employeeId || (data.length > 0 ? data[0].employee_id : "");
      if (focusFor) {
        const focusParams = new URLSearchParams({ employee_id: focusFor });
        if (officeId) focusParams.set("office_id", officeId);
        try {
          const focusRes = await fetch(`${API_URL}/focus/daily?${focusParams.toString()}`);
          setFocus(focusRes.ok ? ((await focusRes.json()) as FocusDaily) : null);
        } catch {
          setFocus(null);
        }
      } else {
        setFocus(null);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load activity data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchData();
    const t = setInterval(() => { void fetchData(); }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officeId, employeeId, machineId, appUsageDay]);

  // Hard filter: only this laptop is ever displayed, whatever the filter
  // input holds. Every derived list below reads from scopedRecords.
  const scopedRecords = useMemo(
    () => records.filter((r) => r.machine_id === THIS_MACHINE),
    [records]
  );

  const activityRows = useMemo(
    () => scopedRecords.filter((r) => r.event_type === "activity"),
    [scopedRecords]
  );

  // The strain summary can repeat a machine/employee pair as new samples
  // arrive. Keep only the newest sample per pair, then keep this laptop only.
  const latestStrain = useMemo(() => {
    const latest = new Map<string, StrainSummary>();
    for (const s of strain) {
      const key = `${s.machine_id}/${s.employee_id}`;
      const prev = latest.get(key);
      if (!prev || (s.last_seen ?? "") > (prev.last_seen ?? "")) {
        latest.set(key, s);
      }
    }
    return [...latest.values()].sort((a, b) =>
      `${a.employee_id}${a.machine_id}`.localeCompare(`${b.employee_id}${b.machine_id}`),
    );
  }, [strain]);

  const scopedStrain = useMemo(
    () => latestStrain.filter((s) => s.machine_id === THIS_MACHINE),
    [latestStrain]
  );

  // First seen (earliest start) and last seen (latest end) per app, computed
  // in the frontend from the already-fetched activity records. The backend
  // app-usage endpoint does not return these, and it is not changed.
  const seenByApp = useMemo(() => {
    const map = new Map<string, { first: string; last: string }>();
    for (const r of activityRows) {
      const key = normalizeApp(r.app_name);
      if (!key) continue;
      const startMs = new Date(r.start_time).getTime();
      const endMs = new Date(r.end_time).getTime();
      if (Number.isNaN(startMs) || Number.isNaN(endMs)) continue;
      const cur = map.get(key);
      if (!cur) {
        map.set(key, { first: r.start_time, last: r.end_time });
      } else {
        if (startMs < new Date(cur.first).getTime()) cur.first = r.start_time;
        if (endMs > new Date(cur.last).getTime()) cur.last = r.end_time;
      }
    }
    return map;
  }, [activityRows]);

  // This laptop only, most used first, each row carrying its first/last seen.
  const scopedAppUsage = useMemo(() => {
    const rows = appUsage
      .filter((item) => item.machine_id === THIS_MACHINE)
      .map((item) => ({
        ...item,
        seen: seenByApp.get(normalizeApp(item.app_name)) ?? null,
      }));
    rows.sort((a, b) => b.minutes - a.minutes);
    return rows;
  }, [appUsage, seenByApp]);

  const maxAppMin = scopedAppUsage.length > 0 ? Math.max(...scopedAppUsage.map((item) => item.minutes)) : 1;
  const totalAppMin = scopedAppUsage.reduce((sum, item) => sum + item.minutes, 0);

  // Latest day present in the fetched records, so the empty state can point
  // at real data when the selected day has none.
  const latestRecordDay = useMemo(() => {
    let latest = "";
    for (const r of scopedRecords) {
      const d = new Date(r.start_time);
      if (Number.isNaN(d.getTime())) continue;
      const pad = (n: number) => String(n).padStart(2, "0");
      const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      if (day > latest) latest = day;
    }
    return latest;
  }, [scopedRecords]);

  const idleStats = useMemo(() => {
    let idleMin = 0;
    let activeMin = 0;
    for (const r of activityRows) {
      const m = minutesBetween(r.start_time, r.end_time);
      if (r.is_idle) idleMin += m;
      else activeMin += m;
    }
    return { idleMin, activeMin };
  }, [activityRows]);

  const idleAlerts = useMemo(() => {
    const now = Date.now();
    // Uses the deduped per-machine list so a machine is named once.
    return scopedStrain.filter((s) => {
      if (!s.is_idle || !s.last_seen) return false;
      const idleMin = (now - new Date(s.last_seen).getTime()) / 60000;
      return idleMin >= idleThresholdMin;
    });
  }, [scopedStrain, idleThresholdMin]);

  const alerts = useMemo(
    () =>
      scopedRecords.filter(
        (r) => r.event_type === "alert" || r.event_type === "break_suggested" || r.event_type === "fatigue_suspected"
      ),
    [scopedRecords]
  );

  const timelineRows = useMemo(() => scopedRecords.slice(0, 100), [scopedRecords]);

  return (
    <div className="rise">
      <section className="panel" aria-label="Activity filters">
        <div className="section-head">
          <h2 className="section-title">Filters</h2>
          <span className="section-meta">This laptop only, {THIS_MACHINE}</span>
          <div className="form-row" style={{ marginLeft: "auto" }}>
            <ExportButton
              endpoint="/exports/activity.csv"
              label="Export CSV"
              filters={{
                employee_id: employeeId || undefined,
                machine_id: machineId || undefined,
                limit: 1000,
              }}
              filename="activity.csv"
            />
            <button type="button" className="btn" onClick={() => { void fetchData(); }}>
              <ArrowClockwise size={15} weight="bold" aria-hidden="true" /> Refresh
            </button>
          </div>
        </div>
        <div className="form-row">
          <Field label="Employee ID">
            <input className="input" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)} placeholder="all" autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Machine ID">
            <input className="input" value={machineId} onChange={(e) => setMachineId(e.target.value)} placeholder={THIS_MACHINE} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Idle alert threshold (min)">
            <input
              className="input"
              type="number"
              min={1}
              value={idleThresholdMin}
              onChange={(e) => setIdleThresholdMin(Number(e.target.value) || 15)}
              style={{ width: 90 }}
            />
          </Field>
        </div>
        <p style={{ color: "var(--text-2)", fontSize: 13, margin: "14px 0 0", maxWidth: "80ch" }}>
          This laptop only, {THIS_MACHINE}. The tables below never show another machine,
          even if the Machine ID filter is cleared.
        </p>
        <p style={{ color: "var(--text-2)", fontSize: 13, margin: "6px 0 0", maxWidth: "80ch" }}>
          Input counts only. The workstation agent never captures keystroke or file
          content, so no content can appear here. Strain level is a heuristic workload
          signal (green under 40, amber 40 to 69, red 70 and up), not a medical measure.
        </p>
      </section>

      {error && (
        <ErrorNotice
          message={`Could not load activity data: ${error}`}
          onRetry={() => { void fetchData(); }}
        />
      )}

      {idleAlerts.length > 0 && (
        <div className="notice notice-warn rise" role="alert">
          <p>
            <strong>Idle alert:</strong> {idleAlerts.length} machine(s) idle past {idleThresholdMin} min:{' '}
            {idleAlerts.map((s) => `${s.employee_id}@${s.machine_id}`).join(", ")}
          </p>
        </div>
      )}

      <Reveal>
        <section className="panel" aria-label="Daily App Usage">
          <div className="section-head">
            <SectionHead title="Daily App Usage" meta={`${THIS_MACHINE} · ${appUsageDay}`} />
            <Field label="UTC date">
              <input className="input" type="date" value={appUsageDay} onChange={(e) => setAppUsageDay(e.target.value)} />
            </Field>
          </div>
          <p style={{ color: "var(--text-2)", fontSize: 13, margin: "-6px 0 16px", maxWidth: "80ch" }}>
            What was used on this laptop, and for how long. First and last seen come from
            the recorded segments for each app.
          </p>
          {loading && scopedAppUsage.length === 0 ? (
            <SkeletonRows rows={4} height={40} />
          ) : scopedAppUsage.length === 0 ? (
            <EmptyState
              title="No active segments"
              body={
                latestRecordDay
                  ? `No app totals for ${appUsageDay} on this laptop. The latest activity in the loaded records is from ${latestRecordDay}; pick that date to see it. Unmapped apps are neutral.`
                  : "Daily app totals appear after the agent reports non-idle windows. Unmapped apps are neutral."
              }
            />
          ) : (
            <>
              <div className="table-wrap">
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>App</th>
                      <th>Category</th>
                      <th>Total time</th>
                      <th>First seen</th>
                      <th>Last seen</th>
                      <th>Machine</th>
                      <th>Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scopedAppUsage.map((item) => (
                      <tr key={`${item.employee_id}/${item.machine_id}/${item.app_name}/${item.category}`}>
                        <td style={{ fontWeight: 600 }}>{item.app_name}</td>
                        <td>
                          <Chip tone={item.category === "work" ? "signal" : "idle"}>{item.category}</Chip>
                        </td>
                        <td className="num" style={{ fontWeight: 600 }}>{formatDuration(item.minutes)}</td>
                        <td className="num">{item.seen ? formatSeen(item.seen.first, appUsageDay) : "-"}</td>
                        <td className="num">{item.seen ? formatSeen(item.seen.last, appUsageDay) : "-"}</td>
                        <td className="id">{item.machine_id}</td>
                        <td style={{ minWidth: 120 }}>
                          <Meter value={item.minutes} max={maxAppMin} color="var(--ice)" label={`${item.app_name} ${formatDuration(item.minutes)}`} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p style={{ color: "var(--text-2)", fontSize: 13, margin: "12px 0 0" }}>
                Total {formatDuration(totalAppMin)} across {scopedAppUsage.length} app{scopedAppUsage.length === 1 ? "" : "s"} on {appUsageDay}.
              </p>
            </>
          )}
        </section>
      </Reveal>

      <div className="grid-2">
        <Reveal>
          <section className="panel" aria-label="Strain per Machine">
            <SectionHead title="Strain per Machine" meta="This laptop only" />
            {loading && scopedStrain.length === 0 ? (
              <SkeletonRows rows={3} height={56} />
            ) : scopedStrain.length === 0 ? (
              <EmptyState title="No strain data" body="Strain summaries appear here once the workstation agent starts reporting." />
            ) : (
              <div className="table-wrap">
                <table className="ledger">
                  <thead>
                    <tr>
                      <th>Employee</th>
                      <th>Machine</th>
                      <th>Strain</th>
                      <th>Level</th>
                      <th>ML stress</th>
                      <th>Last seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scopedStrain.map((s) => {
                      const color =
                        s.strain_level === "green" ? "var(--ok)"
                          : s.strain_level === "amber" ? "var(--warn)"
                            : s.strain_level === "red" ? "var(--bad)"
                              : "var(--text-3)";
                      return (
                        <tr key={`${s.machine_id}/${s.employee_id}`}>
                          <td className="id">{s.employee_id}</td>
                          <td className="id">{s.machine_id}</td>
                          <td>
                            <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                              <Meter value={s.strain_score ?? 0} color={color} label={`Strain ${s.strain_score ?? "unknown"}`} />
                              <span className="num">{s.strain_score ?? "-"}</span>
                            </span>
                          </td>
                          <td><Chip tone={strainDot(s.strain_level)}>{s.strain_level || "unknown"}</Chip></td>
                          <td className="num">{s.ml_stress_score ?? "-"}</td>
                          <td className="num">{formatClock(s.last_seen)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </Reveal>

        <Reveal>
          <section className="panel" aria-label="Time split and focus">
            <SectionHead title="Time Split and Focus" meta={focus ? focus.employee_id : undefined} />
            <div className="grid-3">
              <Stat label="Active" value={formatDuration(idleStats.activeMin)} mono />
              <Stat label="Idle" value={formatDuration(idleStats.idleMin)} mono />
              <div className="stat">
                <div className="stat-value mono" style={{ color: focus ? focusColor(focus.focus_score) : undefined }}>
                  {focus ? focus.focus_score : "-"}
                </div>
                <div className="stat-label">Focus today / 100</div>
                <div className="stat-sub">{focus ? `scored on ${focus.date}` : "no focus row for this person"}</div>
              </div>
            </div>
            {focus && (
              <div style={{ marginTop: 14 }}>
                <Meter value={focus.focus_score} color={focusColor(focus.focus_score)} label={`Focus ${focus.focus_score} of 100`} />
              </div>
            )}
            <p style={{ color: "var(--text-2)", fontSize: 13, margin: "14px 0 0", maxWidth: "80ch" }}>
              Focus rewards 25-minute deep-work blocks and penalizes app switching,
              fragmentation and idle time. The full breakdown lives on the Focus tab.
            </p>
            {alerts.length > 0 && (
              <>
                <hr className="divider" />
                <h3 className="section-title" style={{ marginBottom: 10, fontSize: 15 }}>Break reminders and alerts</h3>
                <ul className="spec-list">
                  {alerts.slice(0, 6).map((a) => (
                    <li key={a.id}>
                      <Chip tone={eventTone(a.event_type)}>{a.event_type}</Chip>
                      <span className="spec-def">
                        {a.employee_id}@{a.machine_id} &middot; {new Date(a.start_time).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </Reveal>
      </div>

      <Reveal>
        <section className="panel" aria-label="Activity timeline">
          <SectionHead title="Timeline" meta="latest first, 100 rows, one row per recorded segment" />
          {loading && timelineRows.length === 0 ? (
            <SkeletonRows rows={6} height={48} />
          ) : timelineRows.length === 0 ? (
            <EmptyState title="No timeline rows" body="Activity records will list here once the agent reports in." />
          ) : (
            <div className="table-wrap">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Employee</th>
                    <th>Machine</th>
                    <th>App</th>
                    <th>Window</th>
                    <th>Keys (count)</th>
                    <th>Clicks</th>
                    <th>Idle</th>
                    <th>Strain</th>
                  </tr>
                </thead>
                <tbody>
                  {timelineRows.map((r) => {
                    const isEvent = r.event_type !== "activity";
                    return (
                      <tr key={r.id} className={isEvent ? "row-alert" : undefined}>
                        <td className="num">{new Date(r.start_time).toLocaleString()}</td>
                        <td><Chip tone={eventTone(r.event_type)}>{r.event_type}</Chip></td>
                        <td className="id">{r.employee_id}</td>
                        <td className="id">{r.machine_id}</td>
                        <td>{isEvent ? "-" : r.app_name}</td>
                        <td style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.window_title}</td>
                        <td className="num">{isEvent ? "-" : r.keystrokes}</td>
                        <td className="num">{isEvent ? "-" : r.mouse_clicks}</td>
                        <td>{r.is_idle ? "yes" : "no"}</td>
                        <td>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                            <Dot tone={strainDot(r.strain_level)} />
                            <span className="num">{r.strain_score ?? "-"}</span>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </Reveal>
    </div>
  );
}
