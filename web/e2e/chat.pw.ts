import { expect, test } from "@playwright/test";

// 설계 8장 4단계 완료 기준: 도구 단계·인용이 답 도중에 나타남, 중단 동작.
test("도구 단계와 인용이 답 도중에 나타나고, 중단한 질문은 기록에 남지 않는다", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".alarms li")).toHaveCount(1);
  const turns = page.locator(".turn");

  // 1) 첫 질문을 글이 흐르는 중에 중단한다
  await page.getByRole("button", { name: "알람 전체 분석" }).click();
  await expect(turns.nth(0).locator(".answer")).toBeVisible();
  await page.getByRole("button", { name: "중단" }).click();
  await expect(turns.nth(0).getByText("중단함")).toBeVisible();

  // 2) 다시 물으면 첫 질문으로 다룬다 — 끊긴 차례는 받아들인 답으로 남지 않아 이어지는 질문의 자동 검색이 없다
  await page.getByRole("button", { name: "알람 전체 분석" }).click();
  const second = turns.nth(1);
  await expect(second.locator(".steps li")).toHaveCount(2);
  await expect(second.locator(".steps")).not.toContainText("(자동)");
  // 답이 끝나기 전에 도구 단계와 [1]이 이미 보인다
  await expect(second.locator(".answer .cite").first()).toBeVisible();
  await expect(second.getByText("답하는 중")).toBeVisible();
  await expect(second.locator(".meta")).toHaveCount(0);
  await expect(second.locator(".meta")).toBeVisible();
  // 서버가 중단한 차례를 되돌렸다: 가짜 모델이 기록에서 질문을 하나만 봐 첫 질문의 답을 썼고(메시지를 되돌리지 않으면
  // 이어지는 질문의 답을 쓴다), [1]이 이번 검색(2번째) 조각을 가리킨다(조각 번호를 되돌리지 않으면 중단한 차례의 검색 1)
  await expect(second.locator(".answer")).toContainText("가능한 원인");
  const cite = second.locator(".answer .cite.verified").first();
  await expect(cite).toBeVisible();
  await cite.click();
  await expect(page.locator(".sources li.selected")).toContainText("매뉴얼 3.2");
  await expect(page.locator(".sources li.selected")).toContainText("(검색 2)");
  await expect(page.locator(".sources li.selected .snippet")).toContainText("50 °C 이상 높으면");

  // 3) 이어지는 질문은 에이전트가 먼저 검색한다(자동)
  await page.getByPlaceholder(/질문/).fill("트립을 잠시 우회하고 항해를 계속해도 되나요?");
  await page.getByRole("button", { name: "보내기" }).click();
  const third = turns.nth(2);
  await expect(third.locator(".steps")).toContainText("(자동)");
  await expect(third.locator(".meta")).toBeVisible();
  await expect(third.locator(".answer")).toContainText("우회하지 않는다");
});
