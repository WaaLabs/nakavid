import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "../static/islands",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        "timeline-scrub": new URL(
          "./src/islands/timeline-scrub/main.tsx",
          import.meta.url,
        ).pathname,
        "drag-combine": new URL(
          "./src/islands/drag-combine/main.tsx",
          import.meta.url,
        ).pathname,
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
