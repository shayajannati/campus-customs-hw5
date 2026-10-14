/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Only for isolated tests: the API origin. Defaults to http://localhost:8000. Never put a token here. */
  readonly VITE_API_ORIGIN?: string
}
