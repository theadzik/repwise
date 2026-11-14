import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` against an API started by hand. There is no oauth2-proxy
    // on a developer's machine, so this names the signed-in user itself.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        ws: true,
        headers: { "X-Forwarded-User": "developer" },
      },
    },
  },
});
