import { describe, expect, it } from "vitest";
import { SseParser } from "./sse";

describe("SseParser", () => {
  it("reads several events in one chunk", () => {
    const p = new SseParser();
    const events = p.push('event: conversation\ndata: {"id": "a"}\n\nevent: token\ndata: {"text": "안", "round": 0}\n\n');
    expect(events).toEqual([
      { event: "conversation", data: { id: "a" } },
      { event: "token", data: { text: "안", round: 0 } },
    ]);
  });

  it("joins an event split across chunks, even inside the JSON", () => {
    const p = new SseParser();
    expect(p.push('event: token\ndata: {"text": "우회')).toEqual([]);
    expect(p.push('하지 않는다", "round": 1}\n')).toEqual([]);
    expect(p.push("\nevent: done\n")).toEqual([{ event: "token", data: { text: "우회하지 않는다", round: 1 } }]);
    expect(p.push('data: {"tokens": 3}\n\n')).toEqual([{ event: "done", data: { tokens: 3 } }]);
  });
});
