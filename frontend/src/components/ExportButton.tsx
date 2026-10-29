import { DownloadSimple } from '@phosphor-icons/react';

const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

interface ExportButtonProps {
  /** Export endpoint path, e.g. "/exports/activity.csv" */
  endpoint: string;
  /** Button label */
  label?: string;
  /** Query filters forwarded to the endpoint (undefined values are skipped) */
  filters?: Record<string, string | number | undefined>;
  /** Download filename hint for the browser */
  filename?: string;
}

/**
 * Reusable CSV download link. Builds the export URL from the endpoint plus
 * any active dashboard filters and renders a plain anchor so the browser
 * handles the Content-Disposition download natively.
 */
export default function ExportButton({
  endpoint,
  label = 'Export CSV',
  filters,
  filename,
}: ExportButtonProps) {
  const params = new URLSearchParams();
  if (filters) {
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') {
        params.set(key, String(value));
      }
    }
  }
  const query = params.toString();
  const href = `${API_URL}${endpoint}${query ? `?${query}` : ''}`;

  return (
    <a
      href={href}
      download={filename ?? endpoint.split('/').pop() ?? 'export.csv'}
      className="export-link"
    >
      <DownloadSimple size={15} weight="bold" aria-hidden="true" />
      {label}
    </a>
  );
}
