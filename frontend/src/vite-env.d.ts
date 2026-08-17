/// <reference types="vite/client" />

interface ImportMetaEnv {
  // see .env.example, empty means same-origin relative /api and /ws calls
  readonly VITE_API_BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
