"""로컬 서빙 측정(설계 6.7) — 같은 모델 요청을 설정마다 다시 보내 속도와 VRAM을 잰다.

    uv run python -m evals.serving record                 # S2 첫 차례의 모델 요청을 녹화 → evals/data/serving-s2.json
    uv run python -m evals.serving run --label 기본        # 지금 Ollama(11434)로
    uv run python -m evals.serving run --label fa-q8 --fa 1 --kv q8_0   # 그 설정으로 Ollama를 11435에 따로 띄워서
    uv run python -m evals.serving embed --label 임베딩     # 질의 임베딩을 CPU·GPU에 둘 때

- 입력은 진짜 에이전트가 보낸 요청 두 개다: 첫 호출(시스템 + 질문 + 도구 정의)과 답 호출(도구 결과까지 쌓인 것).
- 차가운: 시스템 메시지 앞에 매번 다른 표시를 붙여 Ollama의 프롬프트 캐시를 피한다(프롬프트 전체를 다시 읽음).
  따뜻한: 바로 같은 요청을 한 번 더 — 에이전트 루프는 앞부분이 같은 요청을 이어 보내므로 이쪽이 실제에 가깝다.
- 생성은 temperature 0, num_predict 256으로 길이를 맞춘다.
- 측정 전후 GPU 메모리와 GPU를 쓰는 다른 프로세스를 함께 적는다 — 다른 앱이 VRAM을 쓰면 모델이 넘쳐 느려진다(설계 6.7).
- flash attention·KV 캐시 형식은 Ollama 서버 설정이라, --fa/--kv를 주면 그 설정으로 서버를 11435에 띄워 재고 끈다.
  기본 서버(11434)에 올라간 모델은 먼저 내려 두 서버가 VRAM을 다투지 않게 한다.
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import shutil
import statistics
import subprocess
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
NUM_PREDICT = 256
# GPU 목록에서 빼는 Windows 셸 프로세스 — 화면을 그리느라 늘 있다
SHELL = {"applicationframehost", "explorer", "searchhost", "shellexperiencehost", "shellhost", "startmenuexperiencehost",
         "textinputhost", "windowsterminal", "snippingtool", "dwm", "ollama", "llama-server", "ollama app"}

HEADER = (
    "| 날짜 | 이름 | 서버 | 모델 | num_ctx | 요청 | 프롬프트 토큰 | 차가운 첫 토큰(초) | 프롬프트 처리(tok/s) "
    "| 따뜻한 첫 토큰(초) | 생성(tok/s) | 적재(초) | 모델 크기·GPU에 올라간 몫 | GPU 메모리 전→후(MiB) | 메모 |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


# ---- 녹화 ----


async def _record() -> None:
    from app.agent import Conversation, run_turn
    from app.llm import ChatOptions, stream_chat
    from app.tools import ToolBox

    calls = []

    async def recording(messages, tools, opts):
        calls.append({"messages": json.loads(json.dumps(messages, ensure_ascii=False)), "tools": tools})
        async for ev in stream_chat(messages, tools, opts):
            yield ev

    opts = ChatOptions()
    async with ToolBox("S2") as box:
        async for _ in run_turn(Conversation(), QUESTION, box, opts, llm=recording):
            pass
    FIXTURE.parent.mkdir(exist_ok=True)
    fixture = {
        "recorded": dt.datetime.now().isoformat(timespec="seconds"),
        "scenario": "S2",
        "question": QUESTION,
        "model": opts.model,
        "requests": {"첫 호출": calls[0], "답 호출": calls[-1]},
    }
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"모델 호출 {len(calls)}번 중 첫 호출과 마지막(답) 호출을 {FIXTURE}에 저장")


# ---- 측정 ----


def cold(request: dict) -> dict:
    """시스템 메시지 앞에 매번 다른 표시를 붙인다 — 앞부분이 달라 Ollama의 프롬프트 캐시가 맞지 않는다."""
    messages = [dict(m) for m in request["messages"]]
    messages[0]["content"] = f"(측정 {uuid.uuid4().hex[:8]})\n" + messages[0]["content"]
    return {**request, "messages": messages}


def body(request: dict, model: str, num_ctx: int, think: bool) -> dict:
    return {
        "model": model,
        "messages": request["messages"],
        "tools": request["tools"],
        "stream": True,
        "think": think,
        "keep_alive": "10m",
        "options": {"num_ctx": num_ctx, "temperature": 0, "seed": 1, "num_predict": NUM_PREDICT},
    }


def once(client: httpx.Client, url: str, payload: dict) -> dict:
    """요청 하나 — 첫 토큰(글·도구 호출·생각 중 먼저 온 것)까지의 시간은 이쪽에서, 나머지는 Ollama가 잰 값."""
    t0 = time.perf_counter()
    first = None
    final = None
    with client.stream("POST", f"{url}/api/chat", json=payload) as res:
        res.raise_for_status()
        for line in res.iter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            msg = chunk.get("message") or {}
            if first is None and (msg.get("content") or msg.get("tool_calls") or msg.get("thinking")):
                first = time.perf_counter() - t0
            if chunk.get("done"):
                final = chunk
    if final is None:
        raise RuntimeError("Ollama 응답이 done 없이 끝났다")
    ns = 1e9
    return {
        "first_s": first if first is not None else time.perf_counter() - t0,
        "prompt_tokens": final.get("prompt_eval_count", 0),
        "prompt_tps": final.get("prompt_eval_count", 0) / max(final.get("prompt_eval_duration", 1) / ns, 1e-9),
        "gen_tps": final.get("eval_count", 0) / max(final.get("eval_duration", 1) / ns, 1e-9),
        "load_s": final.get("load_duration", 0) / ns,
    }


def median(rows: list[dict], key: str) -> float:
    return statistics.median(r[key] for r in rows)


def gpu() -> dict:
    """GPU 메모리와, GPU를 쓰는 다른 프로세스 이름(Windows에서는 프로세스별 메모리가 안 나온다)."""
    try:
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout.split(",")
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader"], capture_output=True, text=True
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return {"used": None, "total": None, "util": None, "apps": []}
    names = sorted({Path(a.strip().strip('"')).stem for a in apps if a.strip() and "Insufficient" not in a})
    return {"used": int(q[0]), "total": int(q[1]), "util": int(q[2]), "apps": names}


def loaded(url: str) -> list[dict]:
    return httpx.get(f"{url}/api/ps", timeout=10).json().get("models", [])


def unload_all(url: str) -> None:
    for m in loaded(url):
        httpx.post(f"{url}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=60)
    for _ in range(60):
        if not loaded(url):
            return
        time.sleep(0.5)
    raise RuntimeError(f"{url}의 모델이 내려가지 않았다")


@contextmanager
def server(fa: str | None, kv: str | None):
    """--fa/--kv가 없으면 지금 서버를 쓴다. 있으면 그 설정으로 Ollama를 따로 띄운다(기본 서버의 모델은 내림)."""
    if fa is None and kv is None:
        yield MAIN_URL, "지금 서버(설정 기본값)"
        return
    unload_all(MAIN_URL)
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{ALT_PORT}"}
    if fa is not None:
        env["OLLAMA_FLASH_ATTENTION"] = fa
    if kv is not None:
        env["OLLAMA_KV_CACHE_TYPE"] = kv
    exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    proc = subprocess.Popen([exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{ALT_PORT}"
    try:
        for _ in range(60):
            try:
                httpx.get(f"{url}/api/version", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("따로 띄운 Ollama가 뜨지 않았다")
        yield url, f"따로 띄움(FLASH_ATTENTION={fa or '기본'}, KV_CACHE_TYPE={kv or '기본'})"
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def measure(fa: str | None, kv: str | None, model: str, num_ctx: int, think: bool, repeat: int) -> tuple[str, list[dict], dict]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with server(fa, kv) as (url, desc), httpx.Client(timeout=600) as client:
        unload_all(url)
        before = gpu()
        load = once(client, url, body(cold(fixture["requests"]["첫 호출"]), model, num_ctx, think))  # 적재까지
        after = gpu()
        ps = next((m for m in loaded(url) if m["name"] == model or m["model"] == model), {})
        rows = []
        for name, request in fixture["requests"].items():
            colds, warms = [], []
            for _ in range(repeat):
                payload = body(cold(request), model, num_ctx, think)
                colds.append(once(client, url, payload))
                warms.append(once(client, url, payload))  # 같은 요청 — 앞부분이 캐시에 있다
            rows.append({
                "request": name,
                "prompt_tokens": colds[0]["prompt_tokens"],
                "cold_first": median(colds, "first_s"),
                "prompt_tps": median(colds, "prompt_tps"),
                "warm_first": median(warms, "first_s"),
                "gen_tps": median(colds + warms, "gen_tps"),
            })
        unload_all(url)
    env = {"load_s": load["load_s"], "before": before, "after": after, "size": ps.get("size"), "size_vram": ps.get("size_vram")}
    return desc, rows, env


def table_rows(label: str, desc: str, model: str, num_ctx: int, think: bool, rows: list[dict], env: dict, note: str) -> str:
    gb = lambda b: f"{b / 2**30:.1f}" if b else "?"  # noqa: E731
    share = f"{gb(env['size'])} GB · {100 * env['size_vram'] / env['size']:.0f}%" if env.get("size") else "?"
    b, a = env["before"], env["after"]
    mem = f"{b['used']}→{a['used']} / {a['total']}" if a.get("used") is not None else "?"
    others = ", ".join(n for n in b["apps"] if n.lower() not in SHELL) or "없음"
    memo = "; ".join(x for x in [note, f"GPU를 쓰는 다른 프로세스: {others}", f"측정 전 GPU 사용률 {b['util']}%" if b.get("util") is not None else ""] if x)
    out = []
    for r in rows:
        clipped = " (잘림)" if r["prompt_tokens"] >= num_ctx - 8 else ""
        out.append(
            f"| {dt.date.today()} | {label} | {desc} | {model}{' · think' if think else ''} | {num_ctx} | {r['request']} "
            f"| {r['prompt_tokens']}{clipped} | {r['cold_first']:.2f} | {r['prompt_tps']:.0f} | {r['warm_first']:.2f} "
            f"| {r['gen_tps']:.1f} | {env['load_s']:.1f} | {share} | {mem} | {memo} |"
        )
    return "\n".join(out) + "\n"


def append(text: str) -> None:
    if not RESULTS.exists():
        RESULTS.write_text("# 로컬 서빙 측정\n\n`uv run python -m evals.serving`의 결과를 한 줄씩 더한다(설계 6.7). 시간은 3번의 중앙값.\n\n" + HEADER, encoding="utf-8")
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(text)


# ---- 임베딩을 CPU·GPU에 둘 때 ----


def embed_compare(model: str, num_ctx: int, repeat: int) -> str:
    """대화 모델을 올려 둔 채 질의를 임베딩한다 — GPU로 하면 대화 모델이 밀려나는가, 그 뒤 대화 요청이 얼마나 늦는가."""
    from rag.embed import MODEL as EMBED_MODEL

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    query = "윤활유 입구 압력이 낮을 때 조치"
    lines = []
    with httpx.Client(timeout=600) as client:
        unload_all(MAIN_URL)
        for place, options in (("CPU(num_gpu 0)", {"num_gpu": 0}), ("GPU", {})):
            once(client, MAIN_URL, body(cold(fixture["requests"]["첫 호출"]), model, num_ctx, False))  # 대화 모델을 올림
            times = []
            for _ in range(repeat):
                t0 = time.perf_counter()
                client.post(f"{MAIN_URL}/api/embed", json={"model": EMBED_MODEL, "input": [query], "options": options}).raise_for_status()
                times.append(time.perf_counter() - t0)
            chat = next((m for m in loaded(MAIN_URL) if m["name"] == model), None)
            after = once(client, MAIN_URL, body(cold(fixture["requests"]["첫 호출"]), model, num_ctx, False))
            state = "내려감" if chat is None else f"GPU에 {100 * chat['size_vram'] / chat['size']:.0f}%"
            lines.append(
                f"| {dt.date.today()} | {place} | 첫 질의 {times[0]:.2f}초, 그 뒤 중앙값 {statistics.median(times[1:] or times):.2f}초 "
                f"| 임베딩 뒤 대화 모델: {state} | 다음 대화 요청의 적재 {after['load_s']:.1f}초, 첫 토큰 {after['first_s']:.2f}초 |"
            )
            unload_all(MAIN_URL)
    head = "\n## 질의 임베딩을 어디에 둘까(bge-m3, 대화 모델을 올려 둔 채)\n\n| 날짜 | 임베딩 | 질의 하나 | 대화 모델 | 그 뒤 대화 |\n|---|---|---|---|---|\n"
    return head + "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="로컬 서빙 측정(설계 6.7)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record", help="S2 첫 차례의 모델 요청을 녹화")
    run = sub.add_parser("run", help="녹화한 요청으로 속도·VRAM 측정")
    emb = sub.add_parser("embed", help="질의 임베딩을 CPU·GPU에 둘 때")
    for p in (run, emb):
        p.add_argument("--model", default=os.environ.get("CHAT_MODEL", "qwen3:8b"))
        p.add_argument("--num-ctx", type=int, default=8192)
        p.add_argument("--repeat", type=int, default=3)
    run.add_argument("--label", required=True)
    run.add_argument("--fa", choices=["0", "1"], help="OLLAMA_FLASH_ATTENTION — 주면 Ollama를 따로 띄운다")
    run.add_argument("--kv", choices=["f16", "q8_0", "q4_0"], help="OLLAMA_KV_CACHE_TYPE — 주면 Ollama를 따로 띄운다")
    run.add_argument("--think", action="store_true")
    run.add_argument("--note", default="", help="측정 환경 메모(켜 둔 앱 등)")
    a = ap.parse_args()
    if a.cmd == "record":
        asyncio.run(_record())
    elif a.cmd == "run":
        desc, rows, env = measure(a.fa, a.kv, a.model, a.num_ctx, a.think, a.repeat)
        text = table_rows(a.label, desc, a.model, a.num_ctx, a.think, rows, env, a.note)
        append(text)
        print(text)
    else:
        text = embed_compare(a.model, a.num_ctx, a.repeat)
        append(text)
        print(text)


if __name__ == "__main__":
    main()
