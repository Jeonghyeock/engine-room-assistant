# S1 · 씨앗 2

## 질문: 지금 울리는 알람을 분석하고 조치를 권고해 줘

- `list_active_alarms({})` → ✓ 알람 1개
- `get_trend({'tag': 'ME_EXH_TEMP_CYL3', 'minutes': 30, 'points': 30})` → ✓ ME_EXH_TEMP_CYL3 30분: 360.3 → 462.6
- `read_sensors({'tags': ['ME_EXH_TEMP_CYL3', 'ME_JCW_TEMP_OUT', 'ME_JCW_TEMP_IN', 'ME_JCW_PRESS_IN', 'ME_JCW_PRESS_OUT', 'ME_JCW_FLOW', 'ME_JCW_LEVEL', 'ME_LO_PRESS_IN', 'ME_LO_TEMP_IN', 'ME_LO_TEMP_OUT', 'ME_LO_FILTER_DP', 'ME_LO_COOLER_DP', 'ME_LO_LEVEL', 'ME_EXH_PRESS', 'ME_EXH_TEMP', 'ME_FO_PRESS_IN', 'ME_FO_PRESS_OUT', 'ME_FO_LEVEL', 'ME_FO_TEMP_IN', 'ME_FO_TEMP_OUT', 'ME_FO_FILTER_DP', 'ME_FO_COOLER_DP', 'ME_FO_FLOW', 'ME_GE_SPEED', 'ME_GE_FREQ', 'ME_GE_VOLT', 'ME_GE_CURRENT', 'ME_GE_TEMP', 'ME_GE_PRESS', 'ME_GE_LEVEL', 'ME_GE_FILTER_DP', 'ME_GE_COOLER_DP', 'ME_GE_FLOW']})` → ✗ Error executing tool read_sensors: 모르는 태그 'ME_JCW_PRESS_OUT'. 비슷한 태그: ME_JCW_PRESS_IN, ME_JCW_TEMP_OUT, ME_LO_PRESS_IN.
- `list_tags({})` → ✓ 태그 20개
- `search_docs({'query': '3번 실린더 배기가스 온도가 높을 때 점검 순서', 'source': 'manual'})` → ✓ 조각 5개 [1, 2, 3, 4, 5]

- 상황: 3번 실린더 배기가스 온도가 462.6°C로 경보 등급이며, 3분 전에 울렸다. 지난 30분 동안 102.3°C 상승해, 1분당 약 5.6°C의 변화가 있었다.  
- 가능한 원인:  
  1. 연료 분사 밸브 불량: 분무가 나빠지거나 분사 뒤에 연료가 새어(후적) 늦게 탄다.  
  2. 배기 밸브 누설·소손: 연소 가스가 새면서 온도가 오른다.  
  3. 피스톤 링 마모로 인한 가스 누설(블로바이).  
  4. 연료 분사 시기 이상.  
  5. 센서 이상: 3번 실린더 값만 갑자기 크게 바뀌고, 다른 값이 그대로라면 센서 이상을 의심해야 한다 [3].  
- 권고 조치:  
  1. 3번 실린더의 온도 추세를 확인한다. 서서히 오르면 분사 밸브 마모를, 갑자기 뛰면 밸브 고착이나 센서 이상을 먼저 의심한다 [1].  
  2. 다른 실린더의 온도와 과급기 입구 온도를 함께 본다. 다른 실린더가 정상이면 공통 원인(과부하, 소기 공기)이 아니다 [1].  
  3. 열전대(온도 센서) 이상이 아닌지 확인한다 [3].  
  4. 경보 설정값을 넘었으면 부하를 줄이는 것을 기관장과 협의한다 [1].  
  5. 다음 정박 때 연료 분사 밸브를 꺼내 점검·교체하도록 계획한다 [1].

- [3] 매뉴얼 3.4 — 3.4 배기가스 온도 센서 점검
- [1] 매뉴얼 3.2 — 3.2 한 실린더만 높을 때

(5번 도구 왕복, 근거 검사로 0번 되돌림, 768 토큰, 23.0 tok/s, 35.7초, 문맥 최대 5467/8192)

검사: 답함 ✓, 도구 ✓, 도구 인자 ✓, 인용 있음 ✓, 미확인 인용 없음 ✓, 필수 근거 ✓, 문맥 여유 ✓, 잘리지 않음 ✓, 전체 ✓, 판정 ✓

- 판정 ✓ 답이 3번 실린더만 다른 실린더보다 온도가 높다고 말하는가? → 원문 '예' (바라는 답: 예)
- 판정 ✓ 답이 3번 실린더의 연료 분사 밸브를 점검하라고 권하는가? → 원문 '예' (바라는 답: 예)
