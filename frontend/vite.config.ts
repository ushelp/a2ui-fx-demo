import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端 FastAPI（见 backend/app.py）
      '/api': 'http://127.0.0.1:8000',
    },
  },
});
