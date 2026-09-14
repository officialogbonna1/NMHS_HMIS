import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Component tests run against jsdom. `globals` so a test file reads the way
  // the rest of the ecosystem's do (describe/it/expect without an import).
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    include: ["src/**/*.test.{js,jsx}"],
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      // Uploaded files (medical test scans) are served by Django, and the
      // chart links to them by root-relative URL.
      "/media": "http://localhost:8000",
    },
  },
});
