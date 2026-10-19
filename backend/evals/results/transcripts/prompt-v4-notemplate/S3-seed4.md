# S3 · 씨앗 4

## 질문: 지금 울리는 알람을 분석하고 조치를 권고해 줘

- `list_active_alarms({})` → ✓ 알람 2개
- `list_tags({'system': 'ME'})` → ✓ 태그 8개
- `read_sensors({'tags': ['ME_JCW_TEMP_OUT', 'ME_RPM', 'ME_EXH_TEMP_CYL1', 'ME_EXH_TEMP_CYL2', 'ME_EXH_TEMP_CYL3', 'ME_EXH_TEMP_CYL4', 'ME_EXH_TEMP_CYL5', 'ME_EXH_TEMP_CYL6', 'ME_EXH_TEMP_TC_IN']})` → ✓ ME_JCW_TEMP_OUT 150.0°C, ME_RPM 79.9rpm, ME_EXH_TEMP_CYL1 357.1°C, ME_EXH_TEMP_CYL2 364.9°C, ME_EXH_TEMP_CYL3 362.0°C, ME_EXH_TEMP_CYL4 366.0°C, ME_EXH_TEMP_CYL5 359.7°C, ME_EXH_TEMP_CYL6 362.0°C, ME_EXH_TEMP_TC_IN 425.4°C
- `get_trend({'tag': 'ME_JCW_TEMP_OUT', 'minutes': 30, 'points': 31})` → ✓ ME_JCW_TEMP_OUT 30분: 83.9 → 150.0
- `search_docs({'query': '재킷 냉각수 출구 온도가 높아 경보가 울렸을 때 조치', 'source': 'manual'})` → ✓ 조각 5개 [1, 2, 3, 4, 5]

- 상황: 재킷 냉각수 출구 온도가 150.0°C로 감속 등급의 알람이 2분 전에 떴다.
- 가능한 원인: 출구 온도가 급격히 상승했으며, 센서 고장 가능성도 있다. 같은 계통의 다른 값과 비교해 보아야 한다.
- 권고 조치: 
  1. 재킷 냉각수 입구 온도를 확인하여 입구 온도도 함께 상승했는지 확인하라. [2]
  2. 재킷 냉각수 입구 압력을 확인하여 압력이 낮은지 확인하라. [2]
  3. 출구 온도가 급격히 상승했으므로 센서 고장 가능성을 고려하고 현장 계기로 실제 온도를 확인하라. [3]

- [2] 매뉴얼 5.2 — 5.2 출구 온도 높음
- [3] 매뉴얼 5.3 — 5.3 센서 고장 판별

(5번 도구 왕복, 근거 검사로 0번 되돌림, 431 토큰, 34.5 tok/s, 14.5초, 문맥 최대 5355/8192)

검사: 답함 ✓, 도구 ✓, 도구 인자 ✓, 인용 있음 ✓, 미확인 인용 없음 ✓, 필수 근거 ✓, 문맥 여유 ✓, 잘리지 않음 ✓, 전체 ✓, 판정 ✓

- 판정 ✓ 답이 센서 고장 가능성을 말하는가? → 원문 '예' (바라는 답: 예)
- 판정 ✓ 답이 현장 계기(온도계)로 실제 온도를 확인하라고 권하는가? → 원문 '예' (바라는 답: 예)
