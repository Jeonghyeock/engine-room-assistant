# engine-room-assistant

선박 기관실 알람 대응 어시스턴트. 알람이 뜨면 가상 선박 자동화 시스템에서 센서값을 읽고(MCP), 매뉴얼과 규정에서 절차를 찾아(RAG), 근거를 붙여 조치를 **권고**한다. 조작은 하지 않는다. 모델·검색·색인이 모두 이 PC 안에서 돈다(Ollama, LanceDB).

> **가상 데이터.** 선박·기관·설정값·매뉴얼은 교육용 가상이다. 규정(46 CFR Part 62)만 실제 원문이다.

설계: [docs/DESIGN.md](docs/DESIGN.md)

## 진행 상태

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 가상 매뉴얼, 문서 수집·검색, 검색 평가 | 완료 |
| 2 | MCP 서버(가상 알람 감시 시스템, 문서 검색), 시나리오 3개 | 완료 |
| 3 | 에이전트 루프 | - |
| 4 | SSE API와 화면 | - |
| 5 | 로컬 서빙 측정 | - |

## 1단계 — 문서 수집·검색

```
data/tags.yaml ─┬─► 매뉴얼 설정값 표 ─► manual.md ─► PDF(Edge headless) ─► Docling ─► 절 단위 조각 ─┐
                └─► (2단계) 가상 알람 감시 시스템                                                     ├─► bge-m3 ─► LanceDB
eCFR API ─► 46 CFR Part 62 XML ─► 조·문단 단위 조각, 표는 행 문장 ─────────────────────────────────────┘
```

- **설정값은 `data/tags.yaml` 한 곳에만.** 매뉴얼의 설정값 표는 이 파일로 만들고, 본문에 설정값 숫자를 직접 쓰지 않는다(테스트로 막음).
- **CFR**: 조(§)를 맨 위 문단 (a)(b)… 단위로 자른다. 하위 문단 (1)(2)·(i)(ii)와 주석은 속한 문단에 붙인다. `(i)`는 `(h)` 다음일 때만 맨 위 문단으로 본다. 표는 "머리글: 값" 문장으로 바꿔 첫 열(계통)별로 묶는다.
- **매뉴얼**: Docling이 꺼낸 제목 번호로 위치(`매뉴얼 4.2`)를 정하고, 설정값 표는 행마다 한 조각으로 둔다. PDF 추출 흔적(줄 끝 줄바꿈, 태그 이름에 끼어든 공백 `ME _ LO`)을 지운다.
- **검색**: 코사인 유사도 상위 k. 질문 임베딩은 CPU로 돌린다 — GPU 8GB에 대화 모델과 함께 올라가지 못해 대화 모델이 내려갔다 올라오는 것을 막으려고.

### 검색 평가 (질문 15개, recall@5)

정답은 조각 id가 아니라 위치(`§62.35-50(c)`, `매뉴얼 4.2`)로 적어, 자르는 방식을 바꿔도 같은 정답표로 비교한다. 언어 조합: 한국어 질문→한국어 매뉴얼 9, 한국어 질문→영문 규정 5, 영어→영어 1.

| 설정 | 조각 | recall@5 | 1위 적중 | 질문당 |
|---|---|---|---|---|
| CFR 조 단위(1500자 넘는 조만 문단으로) | 157 | 0.87 | 0.73 | 66 ms |
| **CFR 문단 단위(기본값)** | 187 | **0.93** | **0.80** | 69 ms |

- 문단 단위로 바꾸자, 긴 조 안에 묻혀 있던 한 문장(§62.25-15(c) "안전 장치가 자동 동작하면 알람")이 1위로 올라왔다.
- 남은 1개는 "고장 나면 어느 쪽으로 동작해야 하나" → §62.30-1(failsafe). 질문에 "failsafe"라는 개념어가 없어 한·영 사이를 잇지 못했다. 정답표를 결과에 맞춰 고치지 않고 남겨 둔다 — 3단계에서 규정을 찾을 때는 모델이 영어로 질의하게 해서 다시 잰다.
- 표 행을 묻는 질문(태그 설정값, failsafe 표)은 위치뿐 아니라 **어느 행인지까지** 정답으로 적어 쟀다. 둘 다 그 행이 1위다.
- LanceDB 전문 검색(FTS)은 Windows에서도 동작한다. 다만 태그 이름 질문(`ME_LO_PRESS_IN 경보 설정값은?`)도 벡터 검색만으로 정확한 행이 1위라 섞지 않았다.
- 문항별 결과: [backend/evals/results/](backend/evals/results/)

## 2단계 — MCP 서버

```
data/tags.yaml ─┐
data/scenarios/ ─┴─► ams.system(알람 판정·센서·추세) ─► MCP 서버 ① ams   list_active_alarms · list_tags · read_sensors · get_trend
data/index/ ──────► rag.store(검색) ──────────────────► MCP 서버 ② docs  search_docs
```

- **모든 도구가 읽기 전용**(`readOnlyHint`)이다. 시나리오는 서버를 띄울 때(`--scenario`)만 정해서, 모델이 부를 수 있는 도구 중 상태를 바꾸는 것은 하나도 없다.
- **오류는 모델이 읽고 고칠 수 있게.** 모르는 태그는 "비슷한 태그: ME_LO_PRESS_IN …", 모르는 계통은 유효한 코드를 담아 `ToolError`로 돌려준다. 색인 없음·손상, Ollama 연결 실패·모델 없음(404)도 각각 다른 말로 알린다.
- **결과는 모두 객체 하나**(`{"alarms": …}`, `{"results": …}`)라, 에이전트가 두 서버를 같은 방식으로 읽는다.

### 시나리오 (지난 30분, 현재 = 마지막 분)

| | 상황 | 현재 알람 | 에이전트가 지켜야 할 것 |
|---|---|---|---|
| S1 | 3번 실린더 배기 온도만 꾸준히 상승 | 3번 경보(≥ 450 °C, 3분 전) | 매뉴얼 근거로 권고 |
| S2 | 필터 막힘 → 윤활유 입구 압력 하강 | 필터 차압 경보(6분 전) → 압력 경보(3분 전) | 트립 우회를 권하지 않음 |
| S3 | 냉각수 출구 온도가 1분 새 150 °C(측정 범위 끝)로 뛰어 고착 | 감속·경보(2분 전) | 센서 고장 가능성과 현장 확인 |

평상시 값은 `data/scenarios/baseline.json` 한 곳에, 시나리오에는 바뀌는 부분만 적는다. 값의 흔들림은 씨앗이 정해져 있어 매번 같다.

### 작은 모델(qwen3:8b)로 확인해 바꾼 것

| 처음 | 모델이 한 일 | 바꾼 것 |
|---|---|---|
| 계통 인자 설명 "ME 주기관, LO 윤활유 …" | 문구 그대로 `system="ME 주기관"`을 넣어 3번 연속 실패 | 코드부터 적음 → 3질문×3회 모두 첫 호출에 `ME`·`LO`·`JCW` |
| 검색 조각 수 `k`를 인자로 | 매뉴얼 질문에 `k=1`을 골라 근거를 스스로 좁힘 | 서버가 5로 정함 |
| 검색 설명 "규정은 영어로 물으면 더 잘 찾는다" | 규정 질문도 한국어로 질의 | **아직 못 풂** — 3단계에서 시스템 프롬프트 지시와 서버 쪽 영어 질의를 평가로 비교 |
| 펌프 상태에 `status: "정지"` | (리뷰에서 지적) 알람 등급 "정지"와 같은 글자 | 상태 태그는 값만 |

PR마다 맥락 없이 따로 돌린 리뷰를 받고, 지적별 반영/유지를 PR 댓글 표로 남겼다([#1](https://github.com/Jeonghyeock/engine-room-assistant/pull/1)~[#5](https://github.com/Jeonghyeock/engine-room-assistant/pull/5)).

## 실행

필요: Python 3.12, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), Edge 또는 Chrome(매뉴얼 PDF 인쇄).

```sh
ollama pull bge-m3            # 임베딩 (3단계부터 qwen3:8b도)
cd backend
uv sync --group ingest        # Docling은 수집에만 쓴다(torch 포함이라 무겁다). 색인이 이미 있으면 uv sync만

uv run python -m ingest.build_manual   # tags.yaml + manual.md → data/sources/manual.pdf
uv run python -m ingest.cfr fetch      # eCFR API → data/sources/cfr-46-part62.xml (받은 날 기준 최신)
uv run python -m ingest.build_index    # 조각 → 임베딩 → data/index/
uv run python -m evals.retrieval       # 검색 평가 → evals/results/
uv run pytest                          # 색인·Ollama가 없으면 실제 검색 테스트 1개만 건너뜀

# MCP 서버 (stdio)
uv run python -m mcp_servers.ams_server --scenario S2
uv run python -m mcp_servers.docs_server
# MCP Inspector로 열어 보기
npx @modelcontextprotocol/inspector uv run python -m mcp_servers.ams_server --scenario S2
```

원문 `data/sources/`(CFR XML 2026-10-14 기준, 매뉴얼 PDF)은 레포에 들어 있어 `fetch`·`build_manual` 없이도 색인을 만들 수 있다.

## 출처

- 46 CFR Part 62 Vital System Automation — eCFR(www.ecfr.gov), 미국 연방 규정(저작권 없음). §62.35-50 표 1의 디젤 기관 세부 알람은 ABS Marine Vessel Rules(저작권 있음)를 참조로만 두고 있어 넣지 않았다.
- 가상 매뉴얼 — 이 레포에서 직접 작성.
