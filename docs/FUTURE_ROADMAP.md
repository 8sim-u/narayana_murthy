# WAMSS Future Roadmap

One system, three senses: the camera sees who is there, the computer knows
what they are doing, and the body tells how they are holding up. This is the
living roadmap. Status is honest: Done means it works, Partial means code
exists but is unproven live, Next means it is the following build, Research
means it comes from outside sources and is not yet decided.

Last updated: 2026-11-13. GitHub main: `8c8f9e20` (identity layer done).

## The three pillars

### 1. Computer app: monitors work on the computer
Status: Partial. The workstation agent (`agent/`) logs activity counts (never
keystroke content), strain score, focus score, and presence. The small
`agent/work_monitor.py` already prints WORKING/IDLE and posts to the backend.
What is still to do:
- Package the work monitor as a real single-file Windows exe (PyInstaller),
  runs at login, silent tray icon, tiny footprint. This is the "small
  executable" the user asked for: not a full app, just a file that checks the
  computer's work state and reports it.
- Full agent as a Windows service with local buffering and batched sync, so
  it keeps working when the network drops.
- Per-app productivity scoring and app usage categories (which apps count as
  work, which as break).
- Attendance automation from first-seen and last-seen per day.

### 2. Private wearable wellness: optional self-coaching
Status: Software path implemented; hardware/live validation intentionally not
performed. BLE heart-rate and short-window RMSSD may be sent only to the
employee's private dashboard after separate local and dashboard consent. These
signals are excluded from activity strain scores and manager-visible alerts.
The feature is not a medical measure and does not diagnose burnout. The private
view includes date-only daily summaries, trend hints, false-positive feedback,
and an optional guided breathing pause. See `docs/WELLNESS_PRIVACY.md` for the
evidence and limits.
What is still to do:
- Validate supported wearable/OS combinations only with explicit employee
  consent and a privacy review; do not add health-derived performance scoring.
- Evaluate HRV trend rules as personal reflection prompts, not diagnoses or
  manager decisions; passive monitoring is not equivalent to active biofeedback.

### 3. Small executable: the lightweight checker
Status: Partial. `agent/work_monitor.py` is stdlib-only and already runs on
the laptop. It is not yet packaged as an exe and it does not yet check stress.
Working definition, to confirm: one small file that checks the computer's work
state (active app, idle time) and, when a watch is connected, the body's
stress state, then posts a small status to the backend every few seconds.
Open question: whether it checks activity only, stress only, or both. The
user has the final pick.

## What is already done (2026-11-13)
- YOLO person detection plus ByteTrack Person N tracking, live on the laptop
  camera, 499 events and 151 person IDs seen.
- Identity layer: face recognition remembers WHO a person is across track
  changes and restarts (vision 73 tests, backend 133 tests, frontend build
  clean, re-identification proof passes).
- Room map with live positions, heatmap, occupancy timeline, dwell time,
  entry snapshots.
- Dashboard (Mumbrane style), API key auth, Docker packaging, alerts
  (email/Telegram/webhook), CSV exports, PDF weekly report, multi-office.
- Policy engine, privacy controls, consent gate, 90-day purge, privacy mode
  (no identity in privacy mode, ever).

## Next builds, in order
1. Laptop deploy of the identity layer and a live enrollment plus recognition
   test with a real person. Blocked on tunnel access and the user being at
   the laptop.
2. Real geofence zones: draw an area on the room map, alert on entry and exit.
   Today zones are only a text field on events.
3. Prove scaling: a second camera and a second computer actually connected
   to the same dashboard.
4. Phone camera proven end to end (IP Webcam URL into the pipeline).
5. Package the work monitor as a Windows exe and auto-start it.
6. Pair a real smartwatch and prove heart rate plus HRV end to end.
7. Fuse HRV with work strain, add break prompts and biofeedback coaching.

## Future ideas, from research (not decided)
These come from outside sources. They are options, not commitments.
- Non-intrusive, outcome-based monitoring: measure tasks completed and output,
  not minute-by-minute activity. The industry is moving this way, and it is
  the privacy-respecting direction for this product.
- AI predictive analytics: detect productivity slumps and bottlenecks before
  they happen, from historical patterns.
- Issue 19 candidate: an optional, roster-scoped supportive check-in can use a
  declining HRV trend plus reduced recorded active time. Active time is an
  imperfect work-pattern proxy; this does not identify high performers, measure
  engagement, or diagnose burnout.
- Self-coaching dashboards: each employee sees their own data, which builds
  trust and removes the "big brother" feeling.
- Real-time feedback loops: instant, data-driven feedback instead of a
  weekly report.
- Emotion recognition from face or voice: the industry lists it as a coming
  trend, but it is privacy-sensitive and would need explicit consent design
  first. Not recommended as a default.
- Predictive workforce analytics: forecast staffing needs months ahead from
  activity trends.
- Verifiable records: hash-chained audit logs for work events, so records
  cannot be silently rewritten. (The blockchain version of this idea exists;
  a simple hash chain is the practical first step.)
- Slack and Teams integrations, landing page plus demo video.

## Sources
- Future trends in employee monitoring (non-intrusive tools, AI insights,
  well-being focus): https://pcsite.co.uk/future-trends-in-employee-monitoring-what-to-expect-in-the-next-decade/
- Productivity monitoring tools 2026 (emotion recognition, biometrics,
  predictive analytics): https://jobs.sahla-dz.com/top-10-productivity-monitoring-tools-bossware-in-2026/
- Modern workforce management 2026 (predictive forecasting, burnout early
  warning, self-coaching dashboards): https://www.prodoscore.com/blog/modern-workforce-management-in-2026-from-employee-tracking-to-data-intelligence/
- Employee monitoring software 2026 (privacy-first shift, burnout
  identification): https://blog.9cv9.com/top-10-employee-monitoring-software-to-try-in-2026/
- HRV and burnout (10 percent decline over 2 to 4 weeks, RMSSD, wearables):
  https://www.myhrv.com/posts/hrv-and-burnout
- Wearable biometrics and mental well-being (HRV, sleep, stress tracking,
  burnout prevention platforms): https://www.garmin.com/en-US/blog/health/garmin-health-glimpse-biometrics-and-mental-well-being/
- Best wearables for stress resilience 2026 (HRV biofeedback effect sizes,
  passive tracking limits): https://pulsetto.tech/blogs/blog/best-wearables-stress-resilience
