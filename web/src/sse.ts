export type SseEvent = { event: string; data: unknown };

/** SSE 글 조각을 이벤트로 바꾼다. 조각이 이벤트 중간에서 잘려 와도 다음 조각과 이어 읽는다.
 *  백엔드(app/api.py)는 `event: 이름\ndata: JSON\n\n` 꼴만 보낸다. */
export class SseParser {
  private buf = "";

  push(text: string): SseEvent[] {
    this.buf += text;
    const out: SseEvent[] = [];
    let end: number;
    while ((end = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, end);
      this.buf = this.buf.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) out.push({ event, data: JSON.parse(data.join("\n")) });
    }
    return out;
  }
}
