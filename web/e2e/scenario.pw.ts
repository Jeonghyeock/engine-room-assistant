import { expect, test } from "@playwright/test";

// 시나리오 바꾸기가 실패하면 서버는 대화를 지우고 도구 상자 없이(current null) 남는다(api.py) — 화면도 그 상태로 맞춘다(#4 리뷰).
// 가짜 도구 상자는 실패하지 않으므로, 실패한 서버의 응답은 브라우저에서 가로채 흉내 낸다.
test("시나리오 바꾸기가 실패하면 옛 대화·알람을 지우고, 같은 시나리오를 다시 고를 수 있다", async ({ page }) => {
  await page.goto("/");
  const select = page.getByRole("combobox", { name: "시나리오" });
  await expect(select).toHaveValue("S1");
  await expect(page.locator(".alarms li")).toHaveCount(1);
  await page.getByRole("button", { name: "알람 전체 분석" }).click();
  await expect(page.locator(".turn .meta")).toBeVisible();

  await page.route("**/api/scenarios/S2/start", (r) => r.fulfill({ status: 503, json: { detail: "시나리오 S2의 도구 서버를 띄우지 못했다" } }));
  await page.route("**/api/scenarios", async (r) => {
    const res = await r.fetch();
    await r.fulfill({ response: res, json: { ...(await res.json()), current: null } });
  });
  await page.route("**/api/alarms", (r) => r.fulfill({ status: 503, json: { detail: "도구 서버가 열려 있지 않다" } }));
  await select.selectOption("S2");

  await expect(page.getByText("시나리오를 바꾸지 못했다")).toBeVisible();
  await expect(select).toHaveValue(""); // 서버에 열린 시나리오가 없다
  await expect(page.locator(".alarms li")).toHaveCount(0); // 옛 시나리오의 알람을 남기지 않는다
  await expect(page.locator(".turn")).toHaveCount(0); // 서버가 지운 대화

  await page.unrouteAll();
  await select.selectOption("S1"); // 바뀌지 않은 셀렉트였다면 같은 값이라 change가 일어나지 않았다
  await expect(select).toHaveValue("S1");
  await expect(page.locator(".alarms li")).toHaveCount(1);
});

test("바꾸기는 됐는데 알람을 못 읽으면 옛 알람을 지우고 그렇게 알린다", async ({ page }) => {
  await page.goto("/");
  const select = page.getByRole("combobox", { name: "시나리오" });
  await expect(page.locator(".alarms li")).toHaveCount(1);

  await page.route("**/api/alarms", (r) => r.fulfill({ status: 503, json: { detail: "시나리오를 바꾸는 중이다" } }));
  await select.selectOption("S2");
  await expect(select).toHaveValue("S2");
  await expect(page.getByText("알람을 읽지 못했다")).toBeVisible();
  await expect(page.getByText("시나리오를 바꾸지 못했다")).toHaveCount(0);
  await expect(page.locator(".alarms li")).toHaveCount(0);

  await page.unrouteAll();
  await select.selectOption("S1"); // 다음 테스트를 위해 처음 시나리오로
  await expect(page.locator(".alarms li")).toHaveCount(1);
});
