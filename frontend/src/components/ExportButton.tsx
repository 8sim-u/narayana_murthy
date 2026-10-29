const API_URL =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

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
 * Reusable CSV download button. Builds the export URL from the endpoint plus
 * any active dashboard filters and renders a plain anchor so the browser
 * handles the Content-Disposition download natively.
 */
export default function ExportButton({
  endpoint,
  label = "Export CSV",
  filters,
  filename,
}: ExportButtonProps) {
  const params = new URLSearchParams();
  if (filters) {
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") {
        params.set(key, String(value));
      }
    }
  }
  const query = params.toString();
  const href = `${API_URL}${endpoint}${query ? `?${query}` : ""}`;

  return (
    <a
      href={href}
      download={filename ?? endpoint.split("/").pop() ?? "export.csv"}
      className="export-button"
      style={{
        display: "inline-block",
        padding: "6px 14px",
        border: "1px solid #1d4ed8",
        borderRadius: 6,
        color: "#1d4ed8",
        background: "#fff",
        textDecoration: "none",
        fontSize: 13,
        fontWeight: 600,
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </a>
  );
}
