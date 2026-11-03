import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중에는 Vite(5173)와 백엔드(uvicorn, 8000)를 따로 띄우고 /api만 백엔드로 넘긴다 — CORS 설정이 필요 없다.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  // Host를 그대로 넘긴다 — 백엔드가 Origin과 Host를 비교해 다른 출처의 POST를 거절한다(api.py same_origin).
  // 문자열로만 적으면 Vite가 changeOrigin: true로 Host를 백엔드 주소로 바꾼다.
  server: { proxy: { "/api": { target: backend, changeOrigin: false } } },
});
