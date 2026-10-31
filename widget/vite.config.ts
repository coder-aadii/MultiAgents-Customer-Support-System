import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ command }) => {
  const isLibBuild = command === "build" && process.env.BUILD_LIB === "true";

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5174,
      allowedHosts: ["widget", "localhost", "127.0.0.1"],
      proxy: {
        "/api": {
          target: process.env.VITE_API_PROXY_TARGET || "http://api:8000",
          changeOrigin: true,
        },
      },
    },
    build: isLibBuild
      ? {
          outDir: "dist/widget",
          sourcemap: true,
          lib: {
            entry: path.resolve(__dirname, "src/mount.tsx"),
            name: "MCSSWidget",
            fileName: "mcss-widget",
            formats: ["iife"],
          },
          rollupOptions: {
            external: [],
            output: {
              exports: "named",
              globals: {},
            },
          },
        }
      : {
          outDir: "dist",
          sourcemap: true,
        },
  };
});
