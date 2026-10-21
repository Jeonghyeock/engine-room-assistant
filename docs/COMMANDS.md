# 명령 모음

README의 "실행"에 없는 명령을 모았다. 따로 적지 않으면 `backend/`에서 실행한다.

## 원문 다시 만들기

원문(`data/sources/`)은 레포에 들어 있다. 다시 만들거나 새로 받을 때만 쓴다.

```sh
# tags.yaml + manual.md → data/sources/manual.pdf (Edge 또는 Chrome으로 인쇄)
uv run python -m ingest.build_manual

# eCFR API → data/sources/cfr-46-part62.xml (받은 날 기준 최신)
uv run python -m ingest.cfr fetch
```

## 테스트

```sh
# 색인·Ollama가 없으면 실제 검색 테스트 1개만 건너뛴다
uv run pytest
```

화면(`web/`):

```sh
npm test                           # Vitest
npx playwright install chromium    # 처음 한 번(Linux는 --with-deps chromium)

# 가짜 모델·가짜 도구로 API(8010)·화면(5180)을 띄워 E2E — Ollama·색인 없이 돈다
npm run e2e
```

## MCP 서버

```sh
uv run python -m mcp_servers.ams_server --scenario S2
uv run python -m mcp_servers.docs_server

# MCP Inspector로 열어 보기(Node.js 22.19 이상)
npx @modelcontextprotocol/inspector uv run python -m mcp_servers.ams_server --scenario S2
```

## 에이전트와 평가

```sh
# 명령줄 에이전트 — 질문을 여러 개 주면 한 대화로 이어서 묻는다
uv run python -m app.cli --scenario S2 "지금 울리는 알람을 분석하고 조치를 권고해 줘" "트립을 잠시 우회하고 항해를 계속해도 되나요?"

# 검색 평가 → evals/results/
uv run python -m evals.retrieval

# 시나리오 평가(S1~S3 × 씨앗 5) → evals/results/
uv run python -m evals.scenarios --label 이름

# 판정 모델 회귀 점검 → evals/results/judge-check.md
uv run python -m evals.judge_check
```

## 서빙 측정 (5단계, Windows 전용)

측정용 Ollama를 11435 포트에 따로 띄우고, 끝나면 `taskkill`로 프로세스 트리째 끈다. Ollama 실행 파일이 PATH에 없으면 `%LOCALAPPDATA%`에서 찾는다. 그래서 Windows에서만 동작한다.

```sh
# S2 차례의 모델 호출을 녹화한다
uv run python -m evals.serving record

# 그 설정으로 Ollama를 띄워 녹화한 차례를 재생하며 잰다
uv run python -m evals.serving run --label 이름 [--model ..] [--num-ctx ..] [--fa 0|1] [--kv f16|q8_0|q4_0]

# 질의 임베딩을 CPU·GPU에 둘 때를 비교한다
uv run python -m evals.serving embed

# 그 설정의 서버로 시나리오 평가(-- 뒤는 evals.scenarios 인자)
uv run python -m evals.serving scenarios --fa 1 --kv q8_0 -- --label 이름 --num-ctx 16384
```

## API 종료

README의 API 명령에 `--timeout-graceful-shutdown 5`를 붙이는 이유: 없으면 Ctrl+C가 돌고 있는 차례가 끝날 때까지 기다린다.

## 진짜 모델로 화면 기록

`web/`에서, API와 `npm run dev -- --host 127.0.0.1`이 떠 있는 채로 실행한다. 화면을 조작해 시간을 잰다.

```sh
node scripts/real-run.mjs 출력폴더
```
