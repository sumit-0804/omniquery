import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // One origin for the browser: /api goes to the FastAPI backend, so no CORS setup.
    proxy: { "/api": "http://127.0.0.1:7666" },
  },
});
