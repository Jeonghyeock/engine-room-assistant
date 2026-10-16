"""Ollama 임베딩(bge-m3, 1024차원, 100개 넘는 언어).

문서 수집 때는 GPU로 한꺼번에 돌리고, 질문 하나를 임베딩할 때는 CPU로 돌린다(cpu=True).
GPU 8GB에 대화 모델과 임베딩 모델이 함께 올라가지 못해서, 검색할 때마다 대화 모델이
내려갔다 다시 올라오는 것을 막기 위해서다.
"""

import os

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
DIM = 1024


def embed(texts: list[str], *, cpu: bool = False, batch: int = 32) -> list[list[float]]:
    out: list[list[float]] = []
    with httpx.Client(timeout=600) as client:
        for i in range(0, len(texts), batch):
            body: dict = {"model": MODEL, "input": texts[i : i + batch]}
            if cpu:
                body["options"] = {"num_gpu": 0}
            res = client.post(f"{OLLAMA_URL}/api/embed", json=body).raise_for_status()
            out.extend(res.json()["embeddings"])
    return out
