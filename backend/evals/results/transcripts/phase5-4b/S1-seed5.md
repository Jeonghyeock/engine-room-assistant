# S1 · 씨앗 5

## 질문: 지금 울리는 알람을 분석하고 조치를 권고해 줘

- `list_active_alarms({})` → ✓ 알람 1개
- `get_trend({'tag': 'ME_EXH_TEMP_CYL3'})` → ✓ ME_EXH_TEMP_CYL3 30분: 360.3 → 462.6
- `list_tags({'system': 'ME'})` → ✓ 태그 8개
- `read_sensors({'tags': ['ME_RPM', 'ME_EXH_TEMP_CYL1', 'ME_EXH_TEMP_CYL2', 'ME_EXH_TEMP_CYL3', 'ME_EXH_TEMP_CYL4', 'ME_EXH_TEMP_CYL5', 'ME_EXH_TEMP_CYL6', 'ME_EXH_TEMP_TC_IN']})` → ✓ ME_RPM 91.9rpm, ME_EXH_TEMP_CYL1 358.8°C, ME_EXH_TEMP_CYL2 364.2°C, ME_EXH_TEMP_CYL3 462.6°C, ME_EXH_TEMP_CYL4 365.0°C, ME_EXH_TEMP_CYL5 359.2°C, ME_EXH_TEMP_CYL6 363.9°C, ME_EXH_TEMP_TC_IN 438.1°C
- `search_docs({'source': 'manual', 'query': '3번 실린더 배기가스 온도가 450°C 이상일 때 할 일'})` → ✓ 조각 5개 [1, 2, 3, 4, 5]

Okay, let me try to work through this. The user wants an analysis of the current alarms and recommended actions. I've done a bunch of tool calls to gather data.

First, the active alarm is ME_EXH_TEMP_CYL3 at 462.6°C (warning, limit ≥450°C), started 3 minutes ago.

Then, get_trend showed that the temperature has been increasing from 360.3°C to 462.6°C over the last 30 minutes, with a max change of 5.6°C per minute.

List_tags for ME system showed all the relevant tags, and read_sensors confirmed that ME_EXH_TEMP_CYL3 is at 462.6°C (warning), while the other cylinders (1-2,4-6) are within normal range (300-400°C). ME_EXH_TEMP_TC_IN is at 438.1°C (within normal 380-460°C).

So the problem is specifically with cylinder 3. The other cylinders are normal, so it's a localized issue.

Then, I did a search_docs query for "3번 실린더 배기가스 온도가 450°C 이상일 때 할 일" and got 5 results. The relevant ones are n1, n2, n3, n4, n5.

Looking at the results:

n1: Just the settings for ME_EXH_TEMP_CYL3 (same as the current alarm).

n2: Details on what to do when one cylinder is 50°C higher than others. This is relevant here because the other cylinders are normal, so it's a single cylinder issue.

n3: Sensor check procedure for exhaust temperature.

n4: When all cylinders are high (not the case here).

n5: Settings for ME_EXH_TEMP_CYL5 (not relevant).

So the main result here is n2. It says that when one cylinder is 50°C higher than the average of others, check the following:

1. Temperature trend: gradual vs sudden change.
2. Other cylinders and intake temperature.
3. Sensor issues (thermal couple).
4. Reduce load with the captain.
5. Plan for fuel injector inspection.

In the current data, ME_EXH_TEMP_CYL3 is 462.6°C, while the others are 358.8-365.0°C (average around 362°C). The difference is 462.6 - 362 ≈ 100.6°C, which is above 50°C, so n2 applies.

Also, the get_trend shows a gradual increase (from 360.3 to 462.6 over 30 minutes), so according to n2, this points to fuel injector wear.

The other cylinders are normal, so it's not a system-wide issue.

The answer should be in Korean with the specified format.

Let me structure it:

- 상황: 3번 실린더 배기가스 온도가 462.6°C(경보), 다른 실린더 온도는 정상(300~400°C)이며, 과급기 입구 온도는 438.1°C(정상)입니다. 3번 실린더 온도 추세는 30분 내 360.3°C에서 462.6°C로 증가했습니다.

- 가능한 원인: 연료 분사 밸브 마모(분사가 나빠지거나 후적 발생), 배기 밸브 누설, 피스톤 링 마모, 연료 분사 시기 이상 등.

- 권고 조치: 
  1. 온도 추세 확인 (서서히 증가 → 분사 밸브 마모)
  2. 다른 실린더 온도 및 과급기 온도 점검 (다른 실린더 정상 → 공통 원인 없음)
  3. 열전대 점검 (3.4 참고)
  4. 기관장과 협의하여 부하 감소
  5. 다음 정박 시 연료 분사 밸브 점검 및 교체 계획

Also, the results from search_docs n2 mentions that the temperature difference is 50°C or more, which is the case here (100.6°C), so it's a problem.

I need to check the rules for the answer format. The answer should be in Korean, using the results [n] where applicable.

In the results, n2 is the most relevant. The text says "한 실린더의 배기가스 온도가 나머지 실린더 평균보다 50 °C 이상 높으면..." which matches the situation here.

So the answer would reference n2 as [2].

Let me write it out.
</think>

- **상황**: 3번 실린더 배기가스 온도가 462.6°C(경보)이며, 다른 실린더(1~2, 4~6) 온도는 정상(300~400°C) 상태입니다. 과급기 입구 온도는 438.1°C(정상)입니다. 3번 실린더 온도는 30분 내 360.3°C에서 462.6°C로 꾸준히 증가했습니다.  
- **가능한 원인**: 3번 실린더 온도가 다른 실린더 평균(약 362°C)보다 100°C 이상 높아지므로, **연료 분사 밸브 마모** 또는 **배기 밸브 누설**이 주요 원인입니다.  
- **권고 조치**:  
  1. 온도 추세 분석: 서서히 증가하는 경우 **연료 분사 밸브 마모**를 우선 점검해야 합니다.  
  2. 다른 실린더 온도 및 과급기 입구 온도를 확인해 공통 원인을 배제합니다.  
  3. 열전대(온도 센서) 이상 여부를 점검합니다(매뉴얼 3.4 참조).  
  4. 기관장과 협의하여 부하를 감소시켜 온도 상승을 억제합니다.  
  5. 다음 정박 시 연료 분사 밸브를 점검·교체할 계획을 수립해야 합니다. [2]  

> **참고**: 3번 실린더 온도 추세가 30분 내에 100°C 이상 증가한 점은 **연료 분사 밸브 마모**나 **배기 밸브 누설**을 시사합니다. 다른 실린더 온도가 정상이므로 기관 전체에 공통된 문제는 아닙니다. [2]에서 명시된 점검 순서를 따르면 효과적으로 문제를 해결할 수 있습니다.

- [2] 매뉴얼 3.2 — 3.2 한 실린더만 높을 때
- [3] 매뉴얼 3.4 — 3.4 배기가스 온도 센서 점검

(5번 도구 왕복, 근거 검사로 0번 되돌림, 5912 토큰, 71.6 tok/s, 91.0초, 문맥 최대 8070/8192)

검사: 답함 ✓, 도구 ✓, 도구 인자 ✓, 인용 있음 ✓, 미확인 인용 없음 ✓, 필수 근거 ✓, 전제 오류 없음 ✓, 함정 없음 ✓, 문맥 여유 ✗, 잘리지 않음 ✓, 전체 ✗, 판정 ✓

- 판정 ✓ 답이 3번 실린더만 다른 실린더보다 온도가 높다고 말하는가? → 원문 '예' (바라는 답: 예)
- 판정 ✓ 답이 3번 실린더의 연료 분사 밸브를 점검하라고 권하는가? → 원문 '예' (바라는 답: 예)
