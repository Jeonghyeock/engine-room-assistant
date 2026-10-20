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

## 질의 임베딩을 어디에 둘까(bge-m3, 대화 모델을 올려 둔 채, 기본 서버)

| 날짜 | 임베딩 | 질의 하나 | 임베딩 뒤 두 모델 | 그 뒤 같은 대화 요청 | GPU 전용 / 공유(MiB) |
|---|---|---|---|---|---|
| 2026-10-20 | CPU(num_gpu 0) | 첫 질의 1.32초(적재 포함), 그 뒤 중앙값 0.03초 | bge-m3 GPU 0% · 대화 모델 GPU 100% | 적재 0.0초, 걸린 시간 0.41초 | 807→6865 / 8188 · 공유 62→510 |
| 2026-10-20 | GPU | 첫 질의 4.81초(적재 포함), 그 뒤 중앙값 0.01초 | bge-m3 GPU 100% · 대화 모델 내려감 | 적재 3.6초, 걸린 시간 4.66초 | 806→6865 / 8188 · 공유 62→517 |
