import type { Citation } from "./api";

export type Piece = { kind: "text"; value: string; bold: boolean } | { kind: "cite"; value: string; n: number; bold: boolean };

// 굵은 글씨: ** 바로 안쪽에 공백이 없고 안에 별표·줄바꿈이 없을 때만("a ** b ** c", "V**2"는 글자 그대로)
const BOLD = /\*\*(?![\s*])([^*\n]+?)(?<!\s)\*\*/g;
// 인용은 백엔드(agent._NUM_GROUP·_numbers)와 같은 규칙이다 — [n]·【n】, 묶음 [1, 2], 범위 [1-3](a ≤ b ≤ a+10), 번호는 1~99
const CITE = /[[【](\d+(?:\s*[-–~]\s*\d+)?(?:\s*[,，]\s*\d+(?:\s*[-–~]\s*\d+)?)*)[\]】]/g;
const RANGE = /^(\d+)(\s*[-–~]\s*)(\d+)$/;
const MAX_CITE_NUMBER = 99;

function cites(s: string, bold: boolean): Piece[] {
  const out: Piece[] = [];
  const text = (value: string) => {
    const prev = out.at(-1);
    if (prev?.kind === "text") prev.value += value;
    else if (value) out.push({ kind: "text", value, bold });
  };
  const cite = (value: string, n: number) => (1 <= n && n <= MAX_CITE_NUMBER ? out.push({ kind: "cite", value, n, bold }) : text(value));
  let last = 0;
  for (const m of s.matchAll(CITE)) {
    text(s.slice(last, m.index));
    last = m.index + m[0].length;
    if (/^\d+$/.test(m[1])) {
      cite(m[0], Number(m[1])); // 번호 하나면 괄호째 누른다
      continue;
    }
    text(m[0][0]);
    for (const part of m[1].split(/(\s*[,，]\s*)/)) {
      const r = part.match(RANGE);
      if (r && Number(r[1]) <= Number(r[3]) && Number(r[3]) <= Number(r[1]) + 10) {
        cite(r[1], Number(r[1])); // 범위는 양 끝만 누른다 — 가운데 번호는 근거 패널에 있다
        text(r[2]);
        cite(r[3], Number(r[3]));
      } else if (/^\d+$/.test(part)) cite(part, Number(part));
      else text(part); // 쉼표, 백엔드가 펼치지 않는 범위
    }
    text(m[0].slice(-1));
  }
  text(s.slice(last));
  return out;
}

/** 답의 [n]을 어떻게 보일지. 끝난 답에서 서버가 인용으로 보지 않은 번호는 글자다(null) —
 *  그대로 두면 검색된 적 없는 번호가 확인된 근거처럼 보였다(#3 리뷰). */
export function citeView(n: number, citations: Citation[], settled: boolean): { cls: string; title: string } | null {
  const c = citations.find((c) => c.n === n);
  if (c === undefined) return settled ? null : { cls: "cite", title: "근거 정보를 기다리는 중" };
  return { cls: c.verified ? "cite verified" : "cite unverified", title: c.locator };
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
