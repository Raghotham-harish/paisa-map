import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base:"/workspace/" so built asset URLs match the nginx `location /workspace/`
// alias in production; the dev proxy lets `npm run dev` talk to a local
// `python3 server.py` on :8080 without CORS (same-origin in prod covers this
// for the built app, but dev runs on a different port).
export default defineConfig({
  plugins: [react()],
  base: "/workspace/",
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
});
