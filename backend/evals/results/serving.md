# 로컬 서빙 측정

`uv run python -m evals.serving`의 결과를 한 줄씩 더한다(설계 6.7). 재는 방법은 그 파일의 docstring. 시간·속도는 반복(기본 3번)의 중앙값, 토큰 수는 첫 반복.

| 날짜 | 이름 | 실제 적용(로그) | 모델 | num_ctx | 적재(초) | 첫 호출: 처음부터 처리 | 이어지는 호출: 증분 처리 | 답 호출: 첫 글(초) | 생성(tok/s) | 차례 전체(초) | 모델 크기·GPU 몫 | GPU 전용 / 공유(MiB) | 메모 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-20 | 8b · 기본 | FA auto→enabled, KV K f16·V f16 1152 MiB | qwen3:8b | 8192 | 2.8 | 3176 tok/s (1833) | 2780 tok/s (새 3001) | 0.43 | 43.1 | 11.2 | 5.8 GB · 100% | 789→6833 / 8188 · 공유 93→549 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 14% |
| 2026-10-20 | 8b · FA 끔 | FA disabled, KV K f16·V f16 1152 MiB | qwen3:8b | 8192 | 3.3 | 2007 tok/s (1830) | 1350 tok/s (새 3001) | 0.94 | 36.7 | 14.5 | 6.3 GB · 92% | 775→6956 / 8188 · 공유 96→629 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 14% |
| 2026-10-20 | 8b · FA+KV q8_0 | FA enabled, KV K q8_0·V q8_0 612 MiB | qwen3:8b | 8192 | 2.6 | 3071 tok/s (1832) | 2741 tok/s (새 3001) | 0.43 | 43.9 | 11.0 | 5.2 GB · 100% | 762→6300 / 8188 · 공유 79→565 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 14% |
| 2026-10-20 | 8b · FA+KV q4_0 | FA enabled, KV K q4_0·V q4_0 324 MiB | qwen3:8b | 8192 | 5.1 | 3136 tok/s (1830) | 2763 tok/s (새 3001) | 0.43 | 44.4 | 9.7 | 5.0 GB · 100% | 779→6012 / 8188 · 공유 111→517 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 19% |
| 2026-10-20 | 8b · 문맥 4k | FA auto→enabled, KV K f16·V f16 576 MiB | qwen3:8b | 4096 | 2.5 | 3232 tok/s (1832) | 1529 tok/s (새 2055) | 0.86 | 46.0 | 11.0 | 5.2 GB · 100% | 779→6260 / 8188 · 공유 61→502 | 답 호출 잘림(3887 < 녹화 4821); LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 13% |
| 2026-10-20 | 8b · 문맥 16k | FA auto→enabled, KV K f16·V f16 2304 MiB | qwen3:8b | 16384 | 3.3 | 2206 tok/s (1830) | 1812 tok/s (새 3001) | 0.64 | 19.7 | 22.2 | 7.3 GB · 80% | 779→6950 / 8188 · 공유 53→1243 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 0% |
| 2026-10-20 | 8b · 문맥 16k+KV q8_0 | FA enabled, KV K q8_0·V q8_0 1224 MiB | qwen3:8b | 16384 | 2.5 | 3140 tok/s (1832) | 2774 tok/s (새 3001) | 0.42 | 44.3 | 10.8 | 5.8 GB · 100% | 764→6943 / 8188 · 공유 74→540 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 14% |
| 2026-10-20 | 8b · 문맥 32k+KV q8_0 | FA enabled, KV K q8_0·V q8_0 2448 MiB | qwen3:8b | 32768 | 3.5 | 1906 tok/s (1832) | 1675 tok/s (새 3001) | 0.70 | 18.0 | 22.5 | 7.6 GB · 76% | 791→6881 / 8188 · 공유 67→1464 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 8% |
| 2026-10-20 | 4b · 기본 | FA auto→enabled, KV K f16·V f16 1152 MiB | qwen3:4b | 8192 | 1.8 | 5339 tok/s (1826) | 4534 tok/s (새 3005) | 0.27 | 73.4 | 11.1 | 3.6 GB · 100% | 810→4647 / 8188 · 공유 50→492 | LDPlayer 끔; GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 6% |
| 2026-10-20 | 4b instruct · 기본 | FA auto→enabled, KV K f16·V f16 1152 MiB | qwen3:4b-instruct-2507-q4_K_M | 8192 | 1.8 | 5221 tok/s (1937) | 4495 tok/s (새 3015) | 0.29 | 69.3 | 6.6 | 3.6 GB · 100% | 1045→4883 / 8188 · 공유 95→536 | LDPlayer 끔; Qwen3-4B-Instruct-2507(생각 없음); GPU를 건드리는 다른 프로세스: IDeAOceanMessenger, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 6% |

행마다 읽을 때 주의(#27 리뷰, 서버 로그로 확인):
- **4b · 기본**의 `qwen3:4b`는 Qwen3-4B-Thinking-2507이다. 템플릿이 늘 `<think>`를 붙여 think=false가 먹지 않아, 생각이 답으로 나오고 도구를 부르는 호출이 모두 상한(128토큰)까지 갔다. 첫 글 0.27초는 추론 글의 시작이다. 생각하지 않는 4b는 **4b instruct · 기본**(Qwen3-4B-Instruct-2507).
- **모델 크기·GPU 몫**은 `/api/ps`의 size_vram/size(메모리 비율)다. 로그로는 FA 끔 92% = 36/37층, 16k f16 80% = 31/37층, 32k q8_0 76% = 29/37층만 GPU에 올라갔다(나머지는 CPU).
- **FA 끔**: KV 크기는 같다(1152 MiB). 어텐션 계산 버퍼가 112 → 316 MiB로 커져 층 1개가 CPU로 갔다 — 속도 저하는 FA 커널이 없는 효과와 층 하나가 CPU로 간 효과가 섞인 값이다.
- **문맥 16k·32k**: 재생한 프롬프트는 최대 약 4.8k라 창을 잡기만 하고 채우지는 않았다. 긴 문맥을 실제로 채웠을 때의 속도는 재지 않았다.
- **문맥 4k**: 답 호출이 4821 → 3887로 잘리고 생성 중 문맥 이동까지 일어나, 증분(새 토큰을 168로 셈 — 실제로는 약 2087 처리)과 첫 글은 비교할 수 없다.
- **차례 전체**는 생성 길이에 달려 있다. 반복마다 표시가 달라 temperature 0에서도 생성량이 흔들린다(셋째 호출 75/37/20토큰). q4_0의 9.7초는 셋째 호출이 매번 20토큰이었기 때문이고, 속도 차이가 아니다. 설정마다 한 번(반복 3)씩만 돌렸으므로 몇 % 차이는 잡음과 가를 수 없다 — 2배(넘칠 때) 같은 차이만 읽는다.
- **GPU 전용 메모리**에는 다른 앱이 쓰는 약 780 MiB가 들어 있다(측정 전 값).

## 질의 임베딩을 어디에 둘까(bge-m3, 대화 모델을 올려 둔 채, 기본 서버)

| 날짜 | 임베딩 | 질의 하나 | 임베딩 뒤 두 모델 | 그 뒤 같은 대화 요청 | GPU 전용 / 공유(MiB) |
|---|---|---|---|---|---|
| 2026-10-20 | CPU(num_gpu 0) | 첫 질의 1.32초(적재 포함), 그 뒤 중앙값 0.03초 | bge-m3 GPU 0% · 대화 모델 GPU 100% | 적재 0.0초, 걸린 시간 0.41초 | 807→6865 / 8188 · 공유 62→510 |
| 2026-10-20 | GPU | 첫 질의 4.81초(적재 포함), 그 뒤 중앙값 0.01초 | bge-m3 GPU 100% · 대화 모델 내려감 | 적재 3.6초, 걸린 시간 4.66초 | 806→6865 / 8188 · 공유 62→517 |
