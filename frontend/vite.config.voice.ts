// Deliberately separate from the production entry/configuration.
import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 5177,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:18765', changeOrigin: false } },
  },
});
