# 로컬 서빙 측정

`uv run python -m evals.serving`의 결과를 한 줄씩 더한다(설계 6.7). 시간은 3번의 중앙값.

| 날짜 | 이름 | 서버 | 모델 | num_ctx | 요청 | 프롬프트 토큰 | 차가운 첫 토큰(초) | 프롬프트 처리(tok/s) | 따뜻한 첫 토큰(초) | 생성(tok/s) | 적재(초) | 모델 크기·GPU에 올라간 몫 | GPU 메모리 전→후(MiB) | 메모 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-20 | 기본 · 에뮬레이터 켬 | 지금 서버(설정 기본값) | qwen3:8b | 8192 | 첫 호출 | 1833 | 9.66 | 212 | 1.00 | 18.5 | 3.2 | 5.8 GB · 100% | 1790→7763 / 8188 | LDPlayer(안드로이드 에뮬레이터) 켜 둠; GPU를 쓰는 다른 프로세스: IDeAOceanMessenger, Ld9BoxHeadless, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 13% |
| 2026-10-20 | 기본 · 에뮬레이터 켬 | 지금 서버(설정 기본값) | qwen3:8b | 8192 | 답 호출 | 4834 | 20.15 | 243 | 0.09 | 16.4 | 3.2 | 5.8 GB · 100% | 1790→7763 / 8188 | LDPlayer(안드로이드 에뮬레이터) 켜 둠; GPU를 쓰는 다른 프로세스: IDeAOceanMessenger, Ld9BoxHeadless, Notion, SourceTree, msedge, msedgewebview2, slack; 측정 전 GPU 사용률 13% |
