/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base path for the API. Defaults to a same-origin '/api/v1'.
   *
   * Note what is NOT here: no mapping or routing API key. VITE_ variables are
   * inlined into the bundle and therefore public. Every provider credential
   * stays on the backend (brief section 20).
   */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
