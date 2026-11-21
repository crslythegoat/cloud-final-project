import { defineConfig } from 'vite';
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  server: { proxy: Object.fromEntries(['categories','products','customers','orders','bots','events','estadisticas','sincronizacion','health','clusters','usuarios','metrics'].map(p => [`/${p}`, 'http://127.0.0.1:8000'])) },
});
