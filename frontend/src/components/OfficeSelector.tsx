// Multi-office dashboard filter. "" = all offices (no office_id query param).
// Suggestions include the built-in "hq" default; any typed value is accepted
// because office_id is free-form on the backend.
import { useId } from "react";

interface OfficeSelectorProps {
  value: string;
  onChange: (officeId: string) => void;
  offices?: string[];
}

export default function OfficeSelector({ value, onChange, offices = [] }: OfficeSelectorProps) {
  const listId = useId();
  const suggestions = Array.from(new Set(['hq', ...offices.filter((o) => o && o.trim())]));
  return (
    <label className="field">
      <span className="field-label">Office</span>
      <input
        className="input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        list={listId}
        value={value}
        placeholder="All offices"
        onChange={(e) => onChange(e.target.value.trim())}
        aria-label="Filter dashboard by office"
      />
      <datalist id={listId}>
        {suggestions.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </label>
  );
}
