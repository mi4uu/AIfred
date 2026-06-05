import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev proxy: /api -> FastAPI core on :9120 (I.web)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:9120",
    },
  },
});
