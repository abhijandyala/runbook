import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/demo/",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/bridge": "http://127.0.0.1:8000",
      "/events": "http://127.0.0.1:8000",
      "/decisions": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000"
    }
  }
});
