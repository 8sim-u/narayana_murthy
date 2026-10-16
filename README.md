# narayana-murthy-surveillance

An AI-powered workspace monitoring prototype designed to track occupancy, activity, and productivity metrics across modern work environments. The platform employs a simulation-first approach to validate event pipelines, detection heuristics, and analytics flows prior to integrating live camera feeds and physical sensor hardware.

## Team Split

- **Person A**: Computer Vision (detection, tracking, camera pipelines, model inference)
- **Person B**: Backend, database, dashboard, and end-to-end integration

## Planned Stack (planned, not yet implemented)

- **Backend**: FastAPI
- **Database**: PostgreSQL
- **Frontend / Dashboard**: React + TypeScript

## Folder Structure

```
narayana-murthy-surveillance/
├── backend/    # Server application, API routes, database models, and event handlers
├── frontend/   # Web dashboard and UI visualizations (React + TypeScript)
├── shared/     # Shared schemas, contracts, utilities, and simulation data
├── docs/       # Architecture documents, API specifications, and setup guides
├── .gitignore  # Git ignore definitions
└── README.md   # Project overview and guidelines
```

## Architectural & Project Rules

1. **Dashboard Data Access**: The dashboard never talks to the database directly; all access is routed through backend APIs.
2. **Event Attribution**: Simulated events must be labelled with `source="simulation"` to maintain separation from real-world camera detections.
3. **Security & Secrets**: No secrets in git (passwords, tokens, and credentials must live in environment files or secret managers).

## Status

Nothing is implemented yet. Project skeleton setup phase.
