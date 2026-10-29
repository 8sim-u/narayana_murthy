// OfficeSelector.tsx (Agent 5, Round 3) - multi-office dashboard filter.
//
// A single touch-friendly control for choosing the office whose data the
// dashboard shows. The empty value means "all offices" (no office_id query
// param is sent). Suggested offices come from the `offices` prop plus the
// built-in "hq" default; any typed value is accepted because office_id is
// free-form on the backend.

interface OfficeSelectorProps {
  value: string;
  onChange: (officeId: string) => void;
  offices?: string[];
}

export default function OfficeSelector({ value, onChange, offices = [] }: OfficeSelectorProps) {
  const suggestions = Array.from(new Set(["hq", ...offices.filter((o) => o && o.trim())]));
  return (
    <label className="office-selector">
      <span className="muted">Office</span>
      <input
        type="text"
        list="office-selector-options"
        value={value}
        placeholder="All offices"
        onChange={(e) => onChange(e.target.value.trim())}
        aria-label="Filter dashboard by office"
      />
      <datalist id="office-selector-options">
        {suggestions.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </label>
  );
}
