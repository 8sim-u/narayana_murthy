import { Field } from './ui';

interface CameraFilterProps {
  cameras: string[];
  selected: string;
  onChange: (cameraId: string) => void;
}

export default function CameraFilter({ cameras, selected, onChange }: CameraFilterProps) {
  return (
    <Field label="Camera">
      <select
        className="select"
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Filter events by camera"
      >
        <option value="">All cameras</option>
        {cameras.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </Field>
  );
}
