/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Base path for the API. Defaults to a same-origin '/api/v1'.
   *
   * VITE_ values are public in the browser bundle. The Google Maps JavaScript
   * key is supplied locally and must be restricted to Maps JavaScript API and
   * the app's allowed HTTP referrers in Google Cloud Console.
   */
  readonly VITE_API_BASE_URL?: string
  readonly VITE_GOOGLE_MAPS_API_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
