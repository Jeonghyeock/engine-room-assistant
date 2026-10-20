"""로컬 서빙 측정(설계 6.7) — 녹화한 에이전트 차례를 설정마다 그대로 다시 보내 속도와 VRAM을 잰다.

    uv run python -m evals.serving record                 # S2 첫 차례의 모델 호출을 모두 녹화 → evals/data/serving-s2.json
    uv run python -m evals.serving run --label 기본        # Ollama를 11435에 따로 띄워(설정 기본값) 잰다
    uv run python -m evals.serving run --label q8 --fa 1 --kv q8_0
    uv run python -m evals.serving embed                  # 질의 임베딩을 CPU·GPU에 둘 때
    uv run python -m evals.serving scenarios --fa 1 --kv q8_0 -- --label 이름 --num-ctx 16384   # 그 설정의 서버로 시나리오 평가

재는 방법 — 에이전트가 실제로 보내는 요청 모양으로:
- 에이전트는 한 차례에서 모델을 여러 번 부르고, 뒤 요청은 앞 요청에 도구 결과를 이어 붙인 것이다. 그래서 녹화한 호출을
  차례대로 다시 보낸다 — 뒤 호출은 앞부분이 Ollama의 프롬프트 캐시에 있어 새로 붙은 부분만 읽는다(증분).
- 반복마다 시스템 메시지 앞에 다른 표시를 붙인다 — 반복 안에서는 캐시가 이어지고, 반복 사이에서는 첫 호출을 처음부터 읽는다.
- 첫 호출: 처음부터 읽는 속도(프롬프트 처리 tok/s). 이어지는 호출: 새로 붙은 토큰을 읽는 속도(증분 tok/s — Ollama는
  캐시에서 맞은 토큰까지 센 전체 길이를 주므로 앞 호출 길이를 빼서 근사). 답 호출: 첫 글이 나올 때까지(이쪽에서 잼)와
  생성 tok/s(256토큰). 차례 전체: 호출들의 시간 합(도구 실행 시간은 빼고).
- 모든 설정을 따로 띄운 Ollama(11435)에서 잰다 — 기본 서버(11434)도 flash attention이 "auto"라 설정 이름만으로는 무엇이
  적용됐는지 모른다. 그 서버의 로그에서 실제로 적용된 flash attention과 KV 캐시 형식을 읽어 함께 적는다.
  기본 서버에 올라간 모델은 먼저 내린다(두 서버가 VRAM을 다투지 않게).
- 측정 전후 GPU 전용 메모리(nvidia-smi)와 공유 메모리(Windows 카운터 — 전용이 넘치면 여기로 간다), GPU를 건드리는
  다른 프로세스를 함께 적는다(설계 6.7).
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "data" / "serving-s2.json"
RESULTS = HERE / "results" / "serving.md"
MAIN_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
ALT_PORT = 11435
QUESTION = "지금 울리는 알람을 분석하고 조치를 권고해 줘"
ANSWER_PREDICT = 256  # 답 호출에서 만들 토큰 — 생성 속도는 이것으로만 잰다(도구 호출은 20토큰 남짓이라 잡음)
STEP_PREDICT = 128  # 도구를 부르는 호출의 상한
# GPU 목록에서 빼는 Windows 셸 프로세스 — 화면을 그리느라 늘 있다
SHELL = {"applicationframehost", "explorer", "searchhost", "shellexperiencehost", "shellhost", "startmenuexperiencehost",
         "textinputhost", "windowsterminal", "snippingtool", "dwm", "ollama", "llama-server", "ollama app"}

HEADER = (
    "| 날짜 | 이름 | 실제 적용(로그) | 모델 | num_ctx | 적재(초) | 첫 호출: 처음부터 처리 | 이어지는 호출: 증분 처리 "
    "| 답 호출: 첫 글(초) | 생성(tok/s) | 차례 전체(초) | 모델 크기·GPU 몫 | GPU 전용 / 공유(MiB) | 메모 |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


# ---- 녹화 ----


async def _record() -> None:
    from app.agent import RECHECK_NOTE, Conversation, run_turn
    from app.llm import ChatOptions, Done, stream_chat
    from app.tools import ToolBox

    calls: list[dict] = []

    async def recording(messages, tools, opts):
        call = {"messages": json.loads(json.dumps(messages, ensure_ascii=False)), "tools": tools, "prompt_tokens": None}
        calls.append(call)
        async for ev in stream_chat(messages, tools, opts):
            if isinstance(ev, Done):
                call["prompt_tokens"] = ev.prompt_tokens
            yield ev

    opts = ChatOptions()
    async with ToolBox("S2") as box:
        async for _ in run_turn(Conversation(), QUESTION, box, opts, llm=recording):
            pass
    # 근거 검사로 되돌린 차례면 마지막 호출에 초안과 검사 메시지가 섞인다 — 그런 녹화는 쓰지 않는다
    note = RECHECK_NOTE.split("{")[0]
    assert all(note not in (m.get("content") or "") for c in calls for m in c["messages"]), "근거 검사가 끼어든 녹화 — 다시 녹화"
    assert calls[-1]["messages"][-1]["role"] == "tool", "마지막 호출이 도구 결과 뒤의 답 호출이 아니다"
    FIXTURE.parent.mkdir(exist_ok=True)
    fixture = {"recorded": dt.datetime.now().isoformat(timespec="seconds"), "scenario": "S2", "question": QUESTION,
               "model": opts.model, "num_ctx": opts.num_ctx, "calls": calls}
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"모델 호출 {len(calls)}번을 {FIXTURE}에 저장 — 프롬프트 토큰 {[c['prompt_tokens'] for c in calls]}")


# ---- 요청 하나 ----


def mark(call: dict, tag: str) -> dict:
    """시스템 메시지 앞에 표시를 붙인 사본 — 표시가 다르면 Ollama의 프롬프트 캐시가 맞지 않는다."""
    messages = [dict(m) for m in call["messages"]]
    messages[0]["content"] = f"(측정 {tag})\n" + messages[0]["content"]
    return {**call, "messages": messages}


def body(call: dict, model: str, num_ctx: int, num_predict: int, think: bool = False) -> dict:
    return {
        "model": model, "messages": call["messages"], "tools": call["tools"], "stream": True, "think": think, "keep_alive": "10m",
        "options": {"num_ctx": num_ctx, "temperature": 0, "seed": 1, "num_predict": num_predict},
    }


def once(client: httpx.Client, url: str, payload: dict) -> dict:
    """요청 하나. 첫 글(content)이 온 때는 이쪽에서 재고, 나머지는 Ollama가 잰 값이다(없으면 None)."""
    t0 = time.perf_counter()
    first_text = None
    final = None
    with client.stream("POST", f"{url}/api/chat", json=payload) as res:
        res.raise_for_status()
        for line in res.iter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise RuntimeError(f"Ollama 오류: {chunk['error']}")
            if first_text is None and (chunk.get("message") or {}).get("content"):
                first_text = time.perf_counter() - t0
            if chunk.get("done"):
                final = chunk
    if final is None:
        raise RuntimeError("Ollama 응답이 done 없이 끝났다")
    sec = lambda k: final[k] / 1e9 if final.get(k) else None  # noqa: E731
    return {
        "wall_s": time.perf_counter() - t0,
        "first_text_s": first_text,
        "prompt_tokens": final.get("prompt_eval_count"),
        "prompt_s": sec("prompt_eval_duration"),
        "gen_tokens": final.get("eval_count"),
        "gen_s": sec("eval_duration"),
        "load_s": sec("load_duration") or 0.0,
    }


def rate(tokens: int | None, seconds: float | None) -> float | None:
    return tokens / seconds if tokens and seconds else None


def summarize(results: list[dict]) -> dict:
    """한 차례(호출 여럿)의 결과를 줄인다. 증분 토큰은 앞 호출과의 길이 차(Ollama는 캐시에서 맞은 것까지 센다)."""
    first, answer = results[0], results[-1]
    pairs = [(a["prompt_tokens"], b["prompt_tokens"]) for a, b in zip(results, results[1:])]
    new_tokens = sum(max(b - a, 0) for a, b in pairs if a and b)
    inc_seconds = sum(r["prompt_s"] or 0 for r in results[1:])
    return {
        "first_tokens": first["prompt_tokens"],
        "first_tps": rate(first["prompt_tokens"], first["prompt_s"]),
        "inc_tokens": new_tokens,
        "inc_tps": rate(new_tokens, inc_seconds),
        "answer_tokens": answer["prompt_tokens"],
        "answer_first_text": answer["first_text_s"],
        "gen_tps": rate(answer["gen_tokens"], answer["gen_s"]),
        "turn_s": sum(r["wall_s"] for r in results),
    }


def replay(client: httpx.Client, url: str, calls: list[dict], model: str, num_ctx: int, tag: str) -> dict:
    """녹화한 차례 하나를 그대로 다시 보낸다. 같은 표시로 이어 보내므로 뒤 호출은 앞부분이 캐시에 있다."""
    last = len(calls) - 1
    return summarize([once(client, url, body(mark(c, tag), model, num_ctx, ANSWER_PREDICT if i == last else STEP_PREDICT))
                      for i, c in enumerate(calls)])


def med(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    return statistics.median(vals) if vals else None


# ---- 환경 ----


def gpu() -> dict:
    """GPU 전용 메모리(nvidia-smi), 공유 메모리(Windows 카운터 — 전용이 넘치면 여기로), GPU를 건드리는 다른 프로세스.
    Windows에서 nvidia-smi의 compute-apps는 그래픽 앱까지 나열하고 프로세스별 메모리는 주지 않는다 — 이름만 적는다."""
    out: dict = {"used": None, "total": None, "util": None, "shared": None, "apps": []}
    try:
        line = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout.splitlines()[0]
        used, total, util = (int(x) for x in line.split(","))
        out.update(used=used, total=total, util=util)
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=20).stdout.splitlines()
        out["apps"] = sorted({Path(a.strip().strip('"')).stem for a in apps if a.strip() and "Insufficient" not in a})
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    try:
        ps = ("(Get-Counter '\\GPU Adapter Memory(*)\\Shared Usage').CounterSamples | "
              "Measure-Object CookedValue -Maximum | ForEach-Object { [int]($_.Maximum / 1MB) }")
        out["shared"] = int(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return out


def loaded(url: str) -> list[dict]:
    return httpx.get(f"{url}/api/ps", timeout=10).json().get("models", [])


def find(models: list[dict], name: str) -> dict | None:
    """이름("qwen3:8b")이나 태그 없는 이름("bge-m3")으로 찾는다."""
    return next((m for m in models if name in (m.get("name"), m.get("model")) or m.get("name", "").split(":")[0] == name), None)


def unload_all(url: str) -> None:
    for m in loaded(url):
        httpx.post(f"{url}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=60)
    for _ in range(60):
        if not loaded(url):
            time.sleep(2)  # 목록이 비어도 VRAM이 풀리는 데는 조금 더 걸린다
            return
        time.sleep(0.5)
    raise RuntimeError(f"{url}의 모델이 내려가지 않았다")


def applied(log: str) -> str:
    """따로 띄운 서버의 로그에서 실제로 적용된 flash attention과 KV 캐시 형식·크기."""
    asked = re.findall(r"flash_attn\s*=\s*(\w+)", log)  # auto / enabled / disabled
    resolved = re.findall(r"Flash Attention (enabled|disabled)", log)  # auto일 때만 나오는 판정
    kv = re.findall(r"K \((\w+)\):\s*([\d.]+) MiB, V \((\w+)\):\s*([\d.]+) MiB", log)
    if asked and asked[-1] == "auto" and resolved:
        fa = f"FA auto→{resolved[-1]}"
    else:
        fa = f"FA {asked[-1]}" if asked else (f"FA {resolved[-1]}" if resolved else "FA ?")
    parts = [fa]
    if kv:  # 다른 num_ctx로 다시 올리면 크기가 여럿 — 나온 순서대로 모두
        sizes = list(dict.fromkeys(f"{float(ks) + float(vs):.0f}" for _, ks, _, vs in kv))
        parts.append(f"KV K {kv[-1][0]}·V {kv[-1][2]} {' / '.join(sizes)} MiB")
    return ", ".join(parts)


@contextmanager
def server(fa: str | None, kv: str | None):
    """설정으로 Ollama를 11435에 따로 띄운다. 끝나면(실패해도) 모델을 내리고 프로세스 트리째 끈다."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", ALT_PORT)) == 0:
            raise RuntimeError(f"{ALT_PORT} 포트를 이미 누가 쓰고 있다 — 남은 서버를 먼저 끈다")
    unload_all(MAIN_URL)
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{ALT_PORT}"}
    for key, val in (("OLLAMA_FLASH_ATTENTION", fa), ("OLLAMA_KV_CACHE_TYPE", kv)):
        if val is None:
            env.pop(key, None)
        else:
            env[key] = val
    exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    fd, name = tempfile.mkstemp(prefix="ollama-bench-", suffix=".log")
    os.close(fd)
    log = Path(name)
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen([exe, "serve"], env=env, stdout=out, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{ALT_PORT}"
    try:
        for _ in range(60):
            if proc.poll() is not None:
                raise RuntimeError(f"따로 띄운 Ollama가 끝났다(종료 코드 {proc.returncode}) — {log}")
            try:
                if httpx.get(f"{url}/api/version", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f"따로 띄운 Ollama가 뜨지 않았다 — {log}")
        yield url, lambda: log.read_text(encoding="utf-8", errors="replace")
    finally:
        try:
            unload_all(url)
        except Exception:  # 서버가 이미 죽었으면 — 아래에서 트리째 끈다
            pass
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=30)


def measure(fa: str | None, kv: str | None, model: str, num_ctx: int, repeat: int) -> dict:
    calls = json.loads(FIXTURE.read_text(encoding="utf-8"))["calls"]
    with server(fa, kv) as (url, read_log), httpx.Client(timeout=900) as client:
        before = gpu()
        warmup = once(client, url, body(mark(calls[0], "적재"), model, num_ctx, 1))  # 모델 적재만
        turns = [replay(client, url, calls, model, num_ctx, uuid.uuid4().hex[:8]) for _ in range(repeat)]
        after = gpu()
        ps = find(loaded(url), model) or {}
        settings = applied(read_log())
    return {"turns": turns, "load_s": warmup["load_s"], "before": before, "after": after, "size": ps.get("size"),
            "size_vram": ps.get("size_vram"), "settings": settings, "recorded_tokens": [c.get("prompt_tokens") for c in calls]}


def table_row(label: str, model: str, num_ctx: int, m: dict, note: str) -> str:
    t = m["turns"]
    f = lambda v, d=1: "?" if v is None else f"{v:.{d}f}"  # noqa: E731
    gb = f"{m['size'] / 2**30:.1f} GB · {100 * m['size_vram'] / m['size']:.0f}%" if m.get("size") else "?"
    b, a = m["before"], m["after"]
    mem = f"{b['used']}→{a['used']} / {a['total']} · 공유 {b['shared']}→{a['shared']}"
    others = ", ".join(n for n in b["apps"] if n.lower() not in SHELL) or "없음"
    # Ollama는 넘친 문맥에서 오래된 메시지를 통째로 버린다 — 녹화 때보다 짧아졌으면 잘린 것
    recorded = m["recorded_tokens"][-1]
    clipped = bool(recorded and t[0]["answer_tokens"] and t[0]["answer_tokens"] < recorded - 32)
    memo = "; ".join(x for x in [
        f"답 호출 잘림({t[0]['answer_tokens']} < 녹화 {recorded})" if clipped else "",
        note,
        f"GPU를 건드리는 다른 프로세스: {others}",
        f"측정 전 GPU 사용률 {b['util']}%" if b.get("util") is not None else "",
    ] if x)
    return (
        f"| {dt.date.today()} | {label} | {m['settings']} | {model} | {num_ctx} | {f(m['load_s'])} "
        f"| {f(med(t, 'first_tps'), 0)} tok/s ({t[0]['first_tokens']}) | {f(med(t, 'inc_tps'), 0)} tok/s (새 {t[0]['inc_tokens']}) "
        f"| {f(med(t, 'answer_first_text'), 2)} | {f(med(t, 'gen_tps'))} | {f(med(t, 'turn_s'))} | {gb} | {mem} | {memo} |\n"
    )


def append(text: str) -> None:
    if not RESULTS.exists():
        RESULTS.write_text(
            "# 로컬 서빙 측정\n\n`uv run python -m evals.serving`의 결과를 한 줄씩 더한다(설계 6.7). 재는 방법은 그 파일의 docstring. "
            "시간·속도는 반복(기본 3번)의 중앙값, 토큰 수는 첫 반복.\n\n" + HEADER, encoding="utf-8")
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(text)


# ---- 임베딩을 CPU·GPU에 둘 때 ----


def embed_compare(model: str, num_ctx: int, repeat: int) -> str:
    """대화 모델을 올려 둔 채 질의를 임베딩한다 — GPU로 하면 대화 모델이 밀려나는가, 그 뒤 대화 요청(같은 요청 — 실제
    에이전트처럼 캐시가 맞을 수 있다)이 얼마나 늦는가. 기본 서버(11434)에서 잰다 — 앱이 실제로 쓰는 서버다."""
    from rag.embed import MODEL as EMBED_MODEL

    calls = json.loads(FIXTURE.read_text(encoding="utf-8"))["calls"]
    query = "윤활유 입구 압력이 낮을 때 조치"
    rows = []
    with httpx.Client(timeout=900) as client:
        for place, options in (("CPU(num_gpu 0)", {"num_gpu": 0}), ("GPU", {})):
            unload_all(MAIN_URL)
            before = gpu()
            payload = body(mark(calls[0], uuid.uuid4().hex[:8]), model, num_ctx, STEP_PREDICT)
            once(client, MAIN_URL, payload)  # 대화 모델을 올리고 캐시를 채움
            times = []
            for _ in range(repeat):
                t0 = time.perf_counter()
                client.post(f"{MAIN_URL}/api/embed", json={"model": EMBED_MODEL, "input": [query], "options": options}).raise_for_status()
                times.append(time.perf_counter() - t0)
            models = loaded(MAIN_URL)
            chat, emb = find(models, model), find(models, EMBED_MODEL)
            after = once(client, MAIN_URL, payload)  # 같은 요청 다시
            g = gpu()
            share = lambda x: "내려감" if x is None else f"GPU {100 * x['size_vram'] / x['size']:.0f}%"  # noqa: E731
            rows.append(
                f"| {dt.date.today()} | {place} | 첫 질의 {times[0]:.2f}초(적재 포함), 그 뒤 중앙값 {statistics.median(times[1:] or times):.2f}초 "
                f"| bge-m3 {share(emb)} · 대화 모델 {share(chat)} | 적재 {after['load_s']:.1f}초, 걸린 시간 {after['wall_s']:.2f}초 "
                f"| {before['used']}→{g['used']} / {g['total']} · 공유 {before['shared']}→{g['shared']} |"
            )
        unload_all(MAIN_URL)
    head = ("\n## 질의 임베딩을 어디에 둘까(bge-m3, 대화 모델을 올려 둔 채, 기본 서버)\n\n"
            "| 날짜 | 임베딩 | 질의 하나 | 임베딩 뒤 두 모델 | 그 뒤 같은 대화 요청 | GPU 전용 / 공유(MiB) |\n|---|---|---|---|---|---|\n")
    return head + "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="로컬 서빙 측정(설계 6.7)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="S2 첫 차례의 모델 호출을 모두 녹화")
    run = sub.add_parser("run", help="녹화한 차례를 다시 보내 속도·VRAM 측정")
    emb = sub.add_parser("embed", help="질의 임베딩을 CPU·GPU에 둘 때")
    for p in (run, emb):
        p.add_argument("--model", default=os.environ.get("CHAT_MODEL", "qwen3:8b"))
        p.add_argument("--num-ctx", type=int, default=8192)
        p.add_argument("--repeat", type=int, default=3)
    run.add_argument("--label", required=True)
    run.add_argument("--fa", choices=["0", "1"], help="OLLAMA_FLASH_ATTENTION")
    run.add_argument("--kv", choices=["f16", "q8_0", "q4_0"], help="OLLAMA_KV_CACHE_TYPE")
    run.add_argument("--note", default="", help="측정 환경 메모(켜 둔 앱 등)")
    sc = sub.add_parser("scenarios", help="그 설정으로 Ollama를 따로 띄워 시나리오 평가(evals.scenarios)를 돌린다")
    sc.add_argument("--fa", choices=["0", "1"])
    sc.add_argument("--kv", choices=["f16", "q8_0", "q4_0"])
    sc.add_argument("rest", nargs=argparse.REMAINDER, help="-- 뒤는 evals.scenarios의 인자")
    a = ap.parse_args()
    if a.cmd == "record":
        asyncio.run(_record())
    elif a.cmd == "scenarios":
        rest = a.rest[1:] if a.rest[:1] == ["--"] else a.rest
        with server(a.fa, a.kv) as (url, read_log):  # 문서 검색의 질의 임베딩도 이 서버로 간다(PASS_ENV)
            subprocess.run([sys.executable, "-m", "evals.scenarios", *rest], env={**os.environ, "OLLAMA_URL": url}, check=True)
            print("서버에 실제 적용:", applied(read_log()))
    elif a.cmd == "run":
        text = table_row(a.label, a.model, a.num_ctx, measure(a.fa, a.kv, a.model, a.num_ctx, a.repeat), a.note)
        append(text)
        print(text)
    else:
        text = embed_compare(a.model, a.num_ctx, a.repeat)
        append(text)
        print(text)


if __name__ == "__main__":
    main()
