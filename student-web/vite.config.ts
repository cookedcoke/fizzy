import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built into fizzy/static/me-app, which Fizzy serves as the student site (see fizzy/portal.py, spa()).
// `npm run dev` runs it on its own and passes the student endpoints (/me/...) to Fizzy on port 8000.
export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/me-app/" : "/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: { outDir: "../fizzy/static/me-app", emptyOutDir: true, assetsDir: "assets" },
  server: { proxy: { "/me": "http://127.0.0.1:8000", "/static": "http://127.0.0.1:8000" } },
}));
