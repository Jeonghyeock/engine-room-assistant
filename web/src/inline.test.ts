import { describe, expect, it } from "vitest";
import { splitInline } from "./inline";

describe("splitInline", () => {
  it("굵은 글씨와 인용을 가른다(진짜 모델의 답에서)", () => {
    expect(splitInline("그 질문에 대한 답은 **안 된다**. 우회할 수 없다 [5].")).toEqual([
      { kind: "text", value: "그 질문에 대한 답은 " },
      { kind: "bold", value: "안 된다" },
      { kind: "text", value: ". 우회할 수 없다 " },
      { kind: "cite", value: "[5]", n: 5 },
      { kind: "text", value: "." },
    ]);
  });

  it("아직 닫히지 않은 **는 글자로 둔다", () => {
    expect(splitInline("답은 **안 된")).toEqual([{ kind: "text", value: "답은 **안 된" }]);
  });
});
