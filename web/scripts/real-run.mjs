// 진짜 모델로 화면을 조작해 시간을 잰다 — CI 밖에서 한 번씩 돌리는 기록용(docs/PROGRESS.md 4단계).
//
//   (터미널 1) cd backend && uv run uvicorn app.api:app --port 8000 --timeout-graceful-shutdown 5
//   (터미널 2) cd web && npm run dev -- --host 127.0.0.1
//   (터미널 3) cd web && node scripts/real-run.mjs ../docs/real-run
//
// S2로 바꾸고 ① 알람 전체 분석 ② 우회 질문 ③ 셋째 질문을 글이 흐를 때 중단한다. 각 시간은 버튼을 누른 때부터
// 첫 단계 줄 / 답 글 / 메타 줄(토큰·문맥)이 보일 때까지다. 기록(run.txt)과 스크린샷을 out 폴더에 남긴다.
import { chromium } from "@playwright/test";
import fs from "node:fs";

const out = process.argv[2] ?? ".";
const url = process.env.WEB_URL ?? "http://127.0.0.1:5173/";
const log = (...a) => {
  const s = `${new Date().toTimeString().slice(0, 8)} ${a.join(" ")}`;
  console.log(s);
  fs.appendFileSync(`${out}/run.txt`, s + "\n");
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
await page.goto(url);
await page.locator("select").selectOption("S2");
await page.locator("select:enabled").waitFor({ timeout: 60000 });
await page.waitForTimeout(500);
log("알람", (await page.locator(".alarms li").allInnerTexts()).join(" / ").replace(/\n/g, " "));
const turns = page.locator(".turn");

async function askAndWait(i, question, shotName) {
  if (question === null) await page.getByRole("button", { name: "알람 전체 분석" }).click();
  else {
    await page.getByLabel("질문").fill(question);
    await page.getByRole("button", { name: "보내기" }).click();
  }
  const t = turns.nth(i);
  const t0 = Date.now();
  const since = () => ((Date.now() - t0) / 1000).toFixed(1);
  await t.locator(".steps li").first().waitFor({ timeout: 180000 });
  log(`[${i}] 첫 단계까지 ${since()}초`);
  await t.locator(".answer").waitFor({ timeout: 300000 });
  log(`[${i}] 답 첫 글까지 ${since()}초`);
  await t.locator(".meta, .error").first().waitFor({ timeout: 600000 });
  log(`[${i}] 끝 ${since()}초`);
  log(`[${i}] 단계`, (await t.locator(".steps li").allInnerTexts()).join(" | "));
  log(`[${i}] 답`, (await t.locator(".answer").allInnerTexts()).join(" ").replace(/\n/g, " ⏎ "));
  log(`[${i}] 경고`, (await t.locator(".warn").allInnerTexts()).join(" | "));
  log(`[${i}] 메타`, await t.locator(".meta, .error").first().innerText());
  const cite = t.locator(".answer .cite.verified").first();
  if (await cite.count()) await cite.click();
  await page.screenshot({ path: `${out}/${shotName}.png`, fullPage: true });
}

await askAndWait(0, null, "1-analyze");
await askAndWait(1, "트립을 잠시 우회하고 항해를 계속해도 되나요?", "2-bypass");
await page.getByLabel("질문").fill("필터를 전환하는 순서를 자세히 알려 줘");
await page.getByRole("button", { name: "보내기" }).click();
await turns.nth(2).locator(".answer").waitFor({ timeout: 300000 });
await page.waitForTimeout(1000);
await page.getByRole("button", { name: "중단" }).click();
log("[2] 중단함");
await turns.nth(2).getByText("중단함").waitFor();
await page.screenshot({ path: `${out}/3-stopped.png`, fullPage: true });
await browser.close();
