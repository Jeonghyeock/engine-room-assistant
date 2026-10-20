import { describe, expect, it } from "vitest";
import { splitInline } from "./inline";

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
