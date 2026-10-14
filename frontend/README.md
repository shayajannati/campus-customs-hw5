# Campus Customs Operations Desk (frontend)

React + Vite + TypeScript. See `../output/design.md` for the design and `../output/harness.md` ("Problem 8") for the backend it talks to.

## Run it

1. Start the backend from the `backend/` folder with an admin token in the environment:

   ```bash
   export CAMPUS_ADMIN_TOKEN='a-long-random-secret-of-16+-characters'
   cd backend
   ../.venv/bin/uvicorn main:app --reload --port 8000
   ```

   (uvicorn 0.54 rejects the bare form `uvicorn main`; see `output/harness.md` 7.1.)

2. Start the frontend:

   ```bash
   cd frontend
   npm install      # first time only
   npm run dev      # http://localhost:5173
   ```

3. Open http://localhost:5173. Reading works at once. Click **Unlock desk** and paste the same token to run agents or approve payments. The token stays in memory only.

The backend allows CORS only from `http://localhost:5173` and only accepts `localhost` / `127.0.0.1` Host headers.

## Checks

```bash
npm run typecheck && npm run lint && npm test && npm run build
```

`VITE_API_ORIGIN` (default `http://localhost:8000`) exists only so isolated tests can point at a test backend on another port. Never put a token in an environment variable or source file.
