import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    define: { __DEMO__: JSON.stringify(mode === "demo") },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: env.BACKEND_URL || "http://127.0.0.1:18000",
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./tests/setup.ts"],
      restoreMocks: true,
    },
  };
});
