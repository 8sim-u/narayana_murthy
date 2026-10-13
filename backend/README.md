# Backend

Minimal FastAPI backend service for the `narayana-murthy-surveillance` project.

## Running the backend

### 1. Create the virtual environment
```bash
python3 -m venv .venv
```

### 2. Activate the virtual environment
```bash
source .venv/bin/activate
```

### 3. Install requirements
```bash
pip install -r requirements.txt
```

### 4. Run the server
```bash
uvicorn app.main:app --port 8000
```

### 5. Run the tests
```bash
pytest -v
```
