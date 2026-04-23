import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const appRoot = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => ({
  plugins: mode === "test" || process.env.VITEST ? [] : [react()],
  resolve: {
    alias: {
      "@shared": fileURLToPath(new URL("../shared/src", import.meta.url)),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 1420,
    strictPort: true,
    fs: {
      allow: [appRoot, repoRoot],
    },
  },
}));
