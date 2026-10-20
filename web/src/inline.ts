export type Piece = { kind: "text"; value: string; bold: boolean } | { kind: "cite"; value: string; n: number; bold: boolean };

// 굵은 글씨: ** 바로 안쪽에 공백이 없고 안에 별표·줄바꿈이 없을 때만("a ** b ** c", "V**2"는 글자 그대로)
const BOLD = /\*\*(?![\s*])([^*\n]+?)(?<!\s)\*\*/g;
const CITE = /\[(\d+)\]/g;

function cites(s: string, bold: boolean): Piece[] {
  const out: Piece[] = [];
  let last = 0;
  for (const m of s.matchAll(CITE)) {
    if (m.index > last) out.push({ kind: "text", value: s.slice(last, m.index), bold });
    out.push({ kind: "cite", value: m[0], n: Number(m[1]), bold });
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push({ kind: "text", value: s.slice(last), bold });
  return out;
}

/** 답 글을 글자·인용([n])으로 가르고, 굵은 글씨(**…**) 안에 있는지 표시한다. 모델은 결론을 **…**로 강조한다 —
 *  그대로 두면 별표가 보였다. 굵은 글씨 안의 [n]도 인용이다. 닫히지 않은 **(글이 아직 흐르는 중)는 글자로 둔다. */
export function splitInline(text: string): Piece[] {
  const out: Piece[] = [];
  let last = 0;
  for (const m of text.matchAll(BOLD)) {
    out.push(...cites(text.slice(last, m.index), false), ...cites(m[1], true));
    last = m.index + m[0].length;
  }
  out.push(...cites(text.slice(last), false));
  return out;
}
