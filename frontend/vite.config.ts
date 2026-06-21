/// <reference types="vitest/config" />
import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Tailwind v4: CSS-first, no tailwind.config.js needed
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Allow importing with "@/..." instead of relative paths like "../../..."
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // Forward /api/* to the FastAPI backend during development.
      // The rewrite strips the leading /api so the backend sees clean paths
      // (e.g. /api/documents → /documents).
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    // Run tests in a browser-like environment so React DOM renders correctly
    environment: "jsdom",
    // Load global test setup (jest-dom matchers, MSW lifecycle hooks)
    setupFiles: "./tests/setup.ts",
    // Allow using describe/it/expect without importing them in each test file
    globals: true,
  },
})
