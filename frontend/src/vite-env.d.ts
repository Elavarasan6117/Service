/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base path for the API. Defaults to a same-origin '/api/v1'.
   *
   * VITE_ values are public in the browser bundle.
   */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
