import requests
import json
from app.services.events import log_event

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"


def research(mission_id: int, mission_title: str):
    log_event(
        mission_id,
        "Researcher",
        "started",
        "Research started",
    )

    prompt = f"""
You are the Research Agent inside NUTTZ OS.

Mission:
{mission_title}

Produce a concise technical research report.

Return JSON only.

Format:

{{
  "summary":"...",
  "technologies":[...],
  "steps":[...],
  "risks":[...]
}}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
        },
        timeout=180,
    )

    response.raise_for_status()

    result = response.json()["response"]

    try:
        report = json.loads(result)
    except Exception:
        report = {
            "summary": result,
            "technologies": [],
            "steps": [],
            "risks": [],
        }

    log_event(
        mission_id,
        "Researcher",
        "completed",
        "Research completed",
    )

    return report


if __name__ == "__main__":
    report = research("Build a secure password manager")

    print("\n===== RESEARCH REPORT =====\n")
    print(json.dumps(report, indent=4))
