# tailBale Frontend

Vite + React + TypeScript single-page app for the tailBale edge orchestrator.

## Scripts

Run from `frontend/`:

```bash
npm run dev         # start the Vite dev server with HMR
npm run build       # type-check (tsc -b) and build for production
npm run lint        # run ESLint over the project
npm test            # run the Vitest suite once
npm run test:watch  # run Vitest in watch mode
```

## Dev server

`npm run dev` serves the app and proxies API calls to the backend: requests to
`/api` are forwarded to `http://localhost:8080` (see `vite.config.ts`). Start the
backend on port 8080 alongside the dev server.

## Path alias

`@/` resolves to `src/` (configured in `vite.config.ts` and `tsconfig`), so
imports can be written as `import { x } from "@/lib/x"`.
