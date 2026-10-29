// Shared design primitives for the WAMSS console (Mumbrane register).
// Visual layer only: data contracts and API calls live in each tab component.
import { useEffect, useRef, type ReactNode } from 'react';
import { WarningCircle, ArrowClockwise, Tray } from '@phosphor-icons/react';

/* ---------------------------------- badges --------------------------------- */

type ChipTone = 'ok' | 'warn' | 'bad' | 'idle' | 'signal' | 'iris';

export function Chip({ tone, children }: { tone: ChipTone; children: ReactNode }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}

export function Dot({ tone, label }: { tone: 'ok' | 'warn' | 'bad' | 'idle'; label?: string }) {
  return <span className={`dot dot-${tone}`} aria-label={label} role={label ? 'img' : undefined} />;
}

/* ---------------------------------- stats ---------------------------------- */

export function Stat({
  label,
  value,
  sub,
  tone,
  mono,
  swellKey,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
  mono?: boolean;
  /** when this changes, the value plays the swell animation once */
  swellKey?: string | number;
}) {
  return (
    <div className="stat">
      <div
        className={`stat-value${mono ? ' mono' : ''}${swellKey !== undefined ? ' swell' : ''}`}
        style={tone ? { color: tone } : undefined}
        key={swellKey}
      >
        {value}
      </div>
      <div className="stat-label">{label}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/* ---------------------------------- meter ---------------------------------- */

export function Meter({
  value,
  max = 100,
  color,
  label,
}: {
  value: number;
  max?: number;
  color: string;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="meter" role="meter" aria-valuenow={value} aria-valuemax={max} aria-label={label}>
      <i style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

/* ------------------------------ motion helpers ----------------------------- */

/** Scroll-triggered riser reveal (Mumbrane riser-in). Adds .in once visible. */
export function Reveal({ children, className = '' }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.classList.add('in');
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add('in');
            io.disconnect();
          }
        }
      },
      { threshold: 0.08 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} className={`reveal ${className}`}>
      {children}
    </div>
  );
}

/* ------------------------------ loading state ------------------------------ */

export function SkeletonRows({ rows = 5, height = 46 }: { rows?: number; height?: number }) {
  return (
    <div aria-hidden="true" style={{ display: 'grid', gap: 8 }}>
      <div className="loadbar" aria-hidden="true"><i /></div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skel" style={{ height, width: `${100 - (i % 3) * 5}%` }} />
      ))}
    </div>
  );
}

export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div aria-hidden="true">
      <div className="loadbar" aria-hidden="true"><i /></div>
      <div className="grid-3">
        {Array.from({ length: count }).map((_, i) => (
          <div key={i} className="skel" style={{ height: 150 }} />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------- empty state ------------------------------- */

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty" role="status">
      <div className="empty-icon">
        <Tray size={22} weight="duotone" aria-hidden="true" />
      </div>
      <div className="empty-title">{title}</div>
      <p className="empty-body">{body}</p>
      {action}
    </div>
  );
}

/* ------------------------------- error state ------------------------------- */

export function ErrorNotice({
  message,
  onRetry,
  tone = 'error',
}: {
  message: string;
  onRetry?: () => void;
  tone?: 'error' | 'warn' | 'info';
}) {
  return (
    <div className={`notice notice-${tone}`} role="alert">
      <WarningCircle size={18} weight="fill" aria-hidden="true" />
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          <ArrowClockwise size={15} weight="bold" aria-hidden="true" /> Retry
        </button>
      )}
    </div>
  );
}

/* ------------------------------ form primitives ---------------------------- */

export function Field({
  label,
  children,
  htmlFor,
}: {
  label: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

export function SectionHead({
  title,
  meta,
  actions,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="section-head">
      <h2 className="section-title">{title}</h2>
      {meta && <span className="section-meta">{meta}</span>}
      {actions}
    </div>
  );
}

/* --------------------------------- helpers --------------------------------- */

export const STATE_TONE: Record<string, ChipTone> = {
  green: 'ok',
  amber: 'warn',
  red: 'bad',
  unknown: 'idle',
};

export function strainTone(level: string | null | undefined): ChipTone {
  return STATE_TONE[(level ?? 'unknown').toLowerCase()] ?? 'idle';
}

/** Dot-safe version of strainTone (strain levels never map to signal/iris). */
export function strainDot(level: string | null | undefined): 'ok' | 'warn' | 'bad' | 'idle' {
  const t = strainTone(level);
  return t === 'signal' || t === 'iris' ? 'idle' : t;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
