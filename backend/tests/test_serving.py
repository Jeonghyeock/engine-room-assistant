from evals.serving import cold, table_rows

REQUEST = {"messages": [{"role": "system", "content": "규칙"}, {"role": "user", "content": "질문"}], "tools": []}


def test_cold_changes_only_the_start_of_the_system_message_and_never_the_fixture():
    a, b = cold(REQUEST), cold(REQUEST)
    assert a["messages"][0]["content"] != b["messages"][0]["content"]  # 매번 달라 캐시가 맞지 않는다
    assert a["messages"][0]["content"].endswith("\n규칙") and a["messages"][1] == REQUEST["messages"][1]
    assert REQUEST["messages"][0]["content"] == "규칙"  # 녹화본은 그대로


def test_table_row_marks_a_clipped_prompt_and_other_gpu_apps():
    rows = [{"request": "답 호출", "prompt_tokens": 4096, "cold_first": 3.2, "prompt_tps": 1500.4, "warm_first": 0.31, "gen_tps": 44.2}]
    env = {"load_s": 2.5, "size": 6 * 2**30, "size_vram": 6 * 2**30,
           "before": {"used": 600, "total": 8188, "util": 3, "apps": ["ollama", "Ld9BoxHeadless"]},
           "after": {"used": 6800, "total": 8188, "util": 40, "apps": []}}
    line = table_rows("4k", "지금 서버", "qwen3:8b", 4096, False, rows, env, "")
    assert "| 4096 (잘림) |" in line and "6.0 GB · 100%" in line and "600→6800 / 8188" in line
    assert "GPU를 쓰는 다른 프로세스: Ld9BoxHeadless" in line
