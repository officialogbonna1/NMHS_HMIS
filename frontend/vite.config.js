import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // The hospital's letterhead is inlined as a data URI; everything else
    // keeps Vite's default (emitted as a file above 4 KB).
    //
    // This is for **printing**. A document is rendered, previewed and sent to
    // a printer in one breath, and an `<img>` that is still fetching — or that
    // 404s because a deployment did not route `/assets/` — prints as a broken
    // box on a hospital letterhead. Inlined, the logo cannot fail to load,
    // needs no static route, and is byte-identical in development, in the
    // production build and in whatever the browser hands the printer.
    // ~15 KB on the bundle, which is the right trade for that.
    //
    // (`?inline` on the import is silently ignored by Vite 5.3, which is why
    // this is here rather than at the import site.)
    assetsInlineLimit: (filePath) =>
      (filePath.includes("Ngozi Maternity and Hospital Services") ? true : undefined),
  },
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
