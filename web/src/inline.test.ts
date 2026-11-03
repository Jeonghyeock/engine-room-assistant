import { describe, expect, it } from "vitest";
import { citeView, splitInline } from "./inline";

const t = (value: string, bold = false) => ({ kind: "text", value, bold });
const c = (n: number, bold = false) => ({ kind: "cite", value: `[${n}]`, n, bold });

describe("splitInline", () => {
  it("굵은 글씨와 인용을 가른다", () => {
    // 앞 절반은 phase3-final-2/S2 우회 답의 원문, 뒤는 지어 붙임
    expect(splitInline("그 질문에 대한 답은 **안 된다**. 우회할 수 없다 [5].")).toEqual([
      t("그 질문에 대한 답은 "),
      t("안 된다", true),
      t(". 우회할 수 없다 "),
      c(5),
      t("."),
    ]);
  });

  it("굵은 글씨 안의 [n]도 인용이다(#23 리뷰)", () => {
    expect(splitInline("결론: **우회할 수 없다 [6]**.")).toEqual([t("결론: "), t("우회할 수 없다 ", true), c(6, true), t(".")]);
  });

  it("앞뒤 글과 상관없이 같은 판정이다 — 안에 별표가 있으면 굵게 하지 않는다", () => {
    expect(splitInline("**가 *나* 다**")).toEqual([t("**가 *나* 다**")]);
    expect(splitInline("**가 *나* 다** 다음")).toEqual([t("**가 *나* 다** 다음")]);
    expect(splitInline("*****")).toEqual([t("*****")]);
  });

  it("** 바로 안쪽이 공백이면 굵게 하지 않는다", () => {
    expect(splitInline("a ** b ** c")).toEqual([t("a ** b ** c")]);
    expect(splitInline("** **")).toEqual([t("** **")]);
  });

  it("아직 닫히지 않은 **는 글자로 둔다", () => {
    expect(splitInline("답은 **안 된")).toEqual([t("답은 **안 된")]);
  });
});

describe("인용 번호 — 백엔드(agent._NUM_GROUP)와 같은 규칙(#3 리뷰)", () => {
  const n = (value: string, num: number) => ({ kind: "cite", value, n: num, bold: false });

  it("1~99만 인용이다 — [0]·[100]·[2026]은 글자", () => {
    expect(splitInline("[0] 기준 [100]개, [2026]년 [99]")).toEqual([t("[0] 기준 [100]개, [2026]년 "), c(99)]);
  });

  it("묶음·범위·전각 괄호도 인용이다 — 번호마다 누를 수 있다", () => {
    expect(splitInline("근거 [1, 2]")).toEqual([t("근거 ["), n("1", 1), t(", "), n("2", 2), t("]")]);
    expect(splitInline("[1-3]")).toEqual([t("["), n("1", 1), t("-"), n("3", 3), t("]")]);
    expect(splitInline("【4】")).toEqual([n("【4】", 4)]);
  });

  it("백엔드가 펼치지 않는 범위(a > b, 10 넘게 벌어짐)는 글자", () => {
    expect(splitInline("[3-1] [1-30]")).toEqual([t("[3-1] [1-30]")]);
  });

  it("전각 쉼표·숫자, 다른 범위 기호와 공백도 백엔드처럼 읽는다", () => {
    expect(splitInline("【１，２】")).toEqual([t("【"), n("１", 1), t("，"), n("２", 2), t("】")]);
    expect(splitInline("[1 – 2, 4~5]")).toEqual([t("["), n("1", 1), t(" – "), n("2", 2), t(", "), n("4", 4), t("~"), n("5", 5), t("]")]);
  });

  it("1~99 경계를 걸친 범위는 범위 안의 끝만 누른다", () => {
    expect(splitInline("[0-3]")).toEqual([t("[0-"), n("3", 3), t("]")]);
    expect(splitInline("[95-100]")).toEqual([t("["), n("95", 95), t("-100]")]);
  });
});

describe("citeView", () => {
  const cit = (num: number, verified: boolean) => ({ n: num, chunk_id: "x", source: "manual", locator: `매뉴얼 4.${num}`, title: "t", snippet: "s", verified });

  it("확인된 인용과 확인되지 않은 인용, 아직 정보가 오지 않은 인용을 구별한다 — 근거 패널에 있는 확인된 인용만 누른다", () => {
    expect(citeView(1, [cit(1, true)], false)).toEqual({ cls: "cite verified", title: "매뉴얼 4.1", button: true });
    expect(citeView(1, [cit(1, false)], true)).toEqual({ cls: "cite unverified", title: "매뉴얼 4.1", button: false });
    expect(citeView(2, [cit(1, true)], false)).toEqual({ cls: "cite", title: "근거 정보를 기다리는 중", button: false });
  });

  it("끝난 답에서 서버가 인용으로 보지 않은 번호는 글자다", () => {
    expect(citeView(2, [cit(1, true)], true)).toBeNull();
  });
});
