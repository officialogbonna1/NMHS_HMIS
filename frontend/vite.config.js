import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      // Uploaded files (medical test scans) are served by Django, and the
      // chart links to them by root-relative URL.
      "/media": "http://localhost:8000",
    },
  },
});
