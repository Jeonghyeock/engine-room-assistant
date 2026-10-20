from evals.serving import applied, find, mark, summarize, table_row

CALL = {"messages": [{"role": "system", "content": "규칙"}, {"role": "user", "content": "질문"}], "tools": []}


def test_mark_changes_only_the_start_of_the_system_message_and_never_the_recording():
    a, b = mark(CALL, "a"), mark(CALL, "b")
    assert a["messages"][0]["content"] != b["messages"][0]["content"]  # 표시가 다르면 캐시가 맞지 않는다
    assert a["messages"][0]["content"].endswith("\n규칙") and a["messages"][1] == CALL["messages"][1]
    assert CALL["messages"][0]["content"] == "규칙"  # 녹화본은 그대로


def r(prompt_tokens, prompt_s, gen_tokens=20, gen_s=1.0, wall_s=2.0, first_text_s=None):
    return {"prompt_tokens": prompt_tokens, "prompt_s": prompt_s, "gen_tokens": gen_tokens, "gen_s": gen_s,
            "wall_s": wall_s, "first_text_s": first_text_s, "load_s": 0.0}


def test_summary_counts_only_the_new_tokens_of_follow_on_calls():
    """Ollama의 prompt_eval_count는 캐시에서 맞은 토큰까지 센 전체 길이다 — 증분은 앞 호출과의 차이로."""
    s = summarize([r(1800, 0.8), r(2000, 0.1), r(3400, 0.5), r(4800, 0.6, gen_tokens=256, gen_s=5.8, first_text_s=0.7)])
    assert s["first_tokens"] == 1800 and round(s["first_tps"]) == 2250
    assert s["inc_tokens"] == 3000 and round(s["inc_tps"]) == 2500  # (200 + 1400 + 1400) / (0.1 + 0.5 + 0.6)
    assert s["answer_first_text"] == 0.7 and round(s["gen_tps"], 1) == 44.1 and s["turn_s"] == 8.0


def test_settings_are_read_from_the_server_log():
    # 실제 로그 줄 — 켜라고 하면 flash_attn = enabled만, auto면 따로 판정 줄이 나온다
    explicit = ("llama_context: flash_attn            = enabled\n"
                "llama_kv_cache: size =  612.00 MiB (  8192 cells,  36 layers,  1/1 seqs), K (q8_0):  306.00 MiB, V (q8_0):  306.00 MiB\n")
    auto = ("llama_context: flash_attn            = auto\n"
            "llama_kv_cache: size = 1152.00 MiB (  8192 cells,  36 layers,  1/1 seqs), K (f16):  576.00 MiB, V (f16):  576.00 MiB\n"
            "resolve_fused_ops: Flash Attention enabled\n")
    assert applied(explicit) == "FA enabled, KV K q8_0·V q8_0 612 MiB"
    assert applied(auto) == "FA auto→enabled, KV K f16·V f16 1152 MiB"
    assert applied("") == "FA ?"
    assert applied(explicit.replace("612.00", "1224.00").replace("306.00", "612.00") + explicit) == "FA enabled, KV K q8_0·V q8_0 1224 / 612 MiB"


def test_find_accepts_a_name_without_tag():
    models = [{"name": "qwen3:8b", "model": "qwen3:8b"}, {"name": "bge-m3:latest", "model": "bge-m3:latest"}]
    assert find(models, "qwen3:8b")["name"] == "qwen3:8b" and find(models, "bge-m3")["name"] == "bge-m3:latest"
    assert find(models, "qwen3:4b") is None


def test_row_marks_a_clipped_answer_call_and_other_gpu_apps():
    turn = {"first_tokens": 1833, "first_tps": 2400.0, "inc_tokens": 3000, "inc_tps": 2600.0, "answer_tokens": 3600,
            "answer_first_text": 0.8, "gen_tps": 44.0, "turn_s": 12.0}
    m = {"turns": [turn], "load_s": 2.5, "size": 6 * 2**30, "size_vram": 6 * 2**30, "settings": "FA enabled",
         "recorded_tokens": [1833, 4834],
         "before": {"used": 600, "total": 8188, "util": 3, "shared": 270, "apps": ["explorer", "Ld9BoxHeadless"]},
         "after": {"used": 6800, "total": 8188, "util": 40, "shared": 280, "apps": []}}
    line = table_row("4k", "qwen3:8b", 4096, m, "")
    assert "답 호출 잘림(3600 < 녹화 4834)" in line and "6.0 GB · 100%" in line
    assert "600→6800 / 8188 · 공유 270→280" in line and "다른 프로세스: Ld9BoxHeadless;" in line


def test_once_reports_ollama_errors_and_missing_durations():
    import json

    import httpx
    import pytest

    from evals.serving import once, rate

    def client(lines):
        body = "\n".join(json.dumps(x) for x in lines).encode()
        return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body)))

    with pytest.raises(RuntimeError, match="Ollama 오류: model not found"):
        once(client([{"error": "model not found"}]), "http://t", {})
    res = once(client([{"message": {"content": "답"}}, {"done": True, "prompt_eval_count": 100, "eval_count": 5}]), "http://t", {})
    assert res["first_text_s"] is not None and res["prompt_s"] is None and rate(res["prompt_tokens"], res["prompt_s"]) is None
