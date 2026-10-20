import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중에는 Vite(5173)와 백엔드(uvicorn, 8000)를 따로 띄우고 /api만 백엔드로 넘긴다 — CORS 설정이 필요 없다.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": backend } },
});
