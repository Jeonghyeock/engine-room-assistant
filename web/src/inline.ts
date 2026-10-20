export type Piece = { kind: "text"; value: string } | { kind: "bold"; value: string } | { kind: "cite"; value: string; n: number };

/** 답 글을 글자·굵은 글씨(**…**)·인용([n])으로 가른다. 모델은 결론을 **…**로 강조한다 — 그대로 두면 별표가 보였다.
 *  닫히지 않은 **는(글이 아직 흐르는 중) 그냥 글자로 둔다. */
export function splitInline(text: string): Piece[] {
  return text
    .split(/(\[\d+\]|\*\*[^*\n]+?\*\*)/g)
    .filter((p) => p !== "")
    .map((p): Piece => {
      const cite = /^\[(\d+)\]$/.exec(p);
      if (cite) return { kind: "cite", value: p, n: Number(cite[1]) };
      if (p.length > 4 && p.startsWith("**") && p.endsWith("**")) return { kind: "bold", value: p.slice(2, -2) };
      return { kind: "text", value: p };
    });
}
