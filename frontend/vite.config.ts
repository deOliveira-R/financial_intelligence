import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// In development /api goes to FI_DEV_API (e.g. the server's tailnet address, or a local
// `fin-intel serve`), else FI_SERVER_URL, with FI_SERVER_API_KEY, from the repository's
// .env or the environment. The built app calls its own origin.
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, "..", "FI_"), ...process.env };
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: env.FI_DEV_API || env.FI_SERVER_URL,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          headers: env.FI_SERVER_API_KEY ? { "X-API-Key": env.FI_SERVER_API_KEY } : {},
        },
      },
    },
  };
});
