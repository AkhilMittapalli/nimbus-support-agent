"""Drive the SSE chat endpoint from the terminal (handy for demos and debugging).

    uv run python scripts/chat_probe.py "message" [--session ID] [--chaos '{"tool_failure":"any"}']
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--session")
    ap.add_argument("--chaos")
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    with httpx.Client(base_url=a.base, timeout=120) as client:
        sid = a.session or client.post("/api/sessions").json()["session_id"]
        body = {"session_id": sid, "message": a.message, "chaos": json.loads(a.chaos) if a.chaos else None}
        answer = ""
        with client.stream("POST", "/api/chat", json=body) as r:
            if r.status_code != 200:
                print("HTTP", r.status_code, r.read().decode())
                sys.exit(1)
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                e = json.loads(line[6:])
                t = e.pop("type")
                if t == "token":
                    answer += e["text"]
                elif t == "trace":
                    tr = e["trace"]
                    print(f"  trace: {tr['latency']} tokens in/out={tr['totals']['input_tokens']}/"
                          f"{tr['totals']['output_tokens']} cost=${tr['totals']['cost_usd']:.5f}")
                elif t == "step.start":
                    print(f"  > {e['tool']} {e['args']}")
                elif t in ("step.end", "step.retry"):
                    print(f"  < {t} {e['tool']} {e.get('status', '')} {e.get('summary', e.get('reason', ''))}")
                elif t not in ("turn.start", "turn.end", "customer"):
                    print(f"  {t}: {e}")
        print(f"session={sid}\nANSWER: {answer}\n")


if __name__ == "__main__":
    main()
