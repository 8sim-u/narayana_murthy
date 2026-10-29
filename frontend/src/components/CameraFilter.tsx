interface CameraFilterProps {
  cameras: string[];
  selected: string;
  onChange: (cameraId: string) => void;
}

export default function CameraFilter({ cameras, selected, onChange }: CameraFilterProps) {
  return (
    <label className="camera-filter">
      Camera
      <select value={selected} onChange={(e) => onChange(e.target.value)}>
        <option value="">All cameras</option>
        {cameras.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
}
