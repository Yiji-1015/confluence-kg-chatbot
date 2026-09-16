#!/usr/bin/env python3
"""
발표용 관측 데이터 생성기.

Grafana 패널은 대부분 `rate(...[5m])`이라 최근 트래픽이 없으면 전부 빈다.
캡처 직전에 이 스크립트로 실제 요청을 흘려 넣는다.

**실서비스 경로로 보낸다.** AI 엔진(`:8000/internal/chat`)을 직접 때리면 Spring의
`http_server_requests`·`http_client_requests`가 쌓이지 않아 01 Overview의 계층별 지연과
04의 전체 요청 지연 패널이 빈 채로 남는다.

질문은 검색 평가셋(`confluence_retrieval_eval_36_indexed.json`)을 **읽기만** 한다.
평가셋을 수정하지 않는다. 검색 품질 평가가 아니라 지연 관측이 목적이므로 정답 여부는
보지 않는다.

    python3 scripts/generate_observability_traffic.py              # 단일 턴 36건
    python3 scripts/generate_observability_traffic.py --multiturn  # 멀티턴 5대화 13턴
    python3 scripts/generate_observability_traffic.py --rounds 2   # 36건 x 2회

멀티턴은 Redis 대화 이력 캐시(`chat_history_cache_total`)를 태우는 유일한 경로다.
단일 턴만 보내면 03 대시보드의 적중률 패널이 0으로 눕는다.
"""
import argparse
import json
import pathlib
import time
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "confluence_retrieval_eval_36_indexed.json"
URL = "http://localhost:8080/api/chat"
USER = "eval-capture"

# 멀티턴 대화. 2턴째부터 지시어("그건", "그 중")를 써서 SEARCH_HISTORY_TURNS 보정도 함께 태운다.
CONVERSATIONS = [
    ["회의록은 비공개로 만들어도 되는 거야?", "그럼 공개 기본값은 어디에 적혀 있어?", "작성할 때 완벽하게 다 적어야 해?"],
    ["본인 결혼하면 경조휴가 며칠이야?", "그건 유급이야?", "배우자 출산은 어떻게 돼?"],
    ["DO-Solution의 4대 핵심 모듈 구성", "그 중 검색을 담당하는 건 뭐야?", "그건 어떤 기술을 써?"],
    ["익명으로 안전이나 고충을 제보하려면 어떤 방식이 있어?", "신고자한테 불이익 주면 어떻게 돼?"],
    ["제휴병원 할인 혜택 안내문 파일로 받을 수 있어?", "가장 최근 버전은 언제 거야?"],
]


def ask(query: str, session_id=None, timeout=120):
    body = json.dumps({"sessionId": session_id, "query": query}).encode()
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "application/json", "X-User-Id": USER},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as res:
        payload = json.loads(res.read())
        return res.status, payload.get("sessionId"), time.perf_counter() - started


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--multiturn", action="store_true", help="멀티턴 5대화 13턴만 실행")
    ap.add_argument("--rounds", type=int, default=1, help="단일 턴 평가셋 반복 횟수")
    args = ap.parse_args()

    elapsed = []
    if args.multiturn:
        for ci, turns in enumerate(CONVERSATIONS, 1):
            session = None
            for ti, q in enumerate(turns, 1):
                code, session, sec = ask(q, session)
                elapsed.append(sec)
                print(f"[대화{ci} 턴{ti}] {code} {sec:5.2f}s {q[:34]}", flush=True)
    else:
        questions = [d["question"] for d in json.loads(DATASET.read_text())]
        for rnd in range(args.rounds):
            for i, q in enumerate(questions, 1):
                code, _, sec = ask(q)
                elapsed.append(sec)
                print(f"[{rnd+1}/{args.rounds} {i:>2}/{len(questions)}] {code} {sec:5.2f}s {q[:34]}", flush=True)

    ordered = sorted(elapsed)
    pct = lambda p: ordered[min(int(len(ordered) * p), len(ordered) - 1)]
    print(f"\n{len(elapsed)}건 | 평균 {sum(ordered)/len(ordered):.2f}s | "
          f"p50 {pct(0.50):.2f}s | p95 {pct(0.95):.2f}s | 최대 {ordered[-1]:.2f}s")
    print("클라이언트 측정값이다. Grafana 수치는 서버 계측이라 조금 다르다.")


if __name__ == "__main__":
    main()
