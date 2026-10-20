import { defineConfig } from "@playwright/test";

// 진짜 API(가짜 모델·가짜 도구, backend/tests/e2e_server.py)와 진짜 화면(Vite)을 띄워 브라우저로 조작한다.
// 개발용(8000·5173)과 겹치지 않는 포트를 쓴다.
const API = 8010;
const WEB = 5180;

export default defineConfig({
  testDir: "e2e",
  testMatch: /.*\.pw\.ts/, // Vitest(*.test.ts)와 섞이지 않게
  timeout: 60_000,
  workers: 1, // 백엔드 하나가 한 번에 한 차례만 받는다(409)
  use: { baseURL: `http://127.0.0.1:${WEB}`, trace: "retain-on-failure", actionTimeout: 10_000 },
  webServer: [
    {
      command: `uv run python -m tests.e2e_server --port ${API}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${API}/api/scenarios`,
      reuseExistingServer: false,
      timeout: 120_000, // 처음 쓰는 컴퓨터면 uv가 의존성을 받는다(CI는 먼저 uv sync)
      env: { PYTHONUTF8: "1" },
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${WEB} --strictPort`,
      url: `http://127.0.0.1:${WEB}`,
      reuseExistingServer: false,
      env: { BACKEND_URL: `http://127.0.0.1:${API}` },
    },
  ],
});
