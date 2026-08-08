import requests
import json

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"


def research(mission_title: str):
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
        return json.loads(result)
    except Exception:
        return {
            "summary": result,
            "technologies": [],
            "steps": [],
            "risks": [],
        }


if __name__ == "__main__":
    report = research("Build a secure password manager")

    print("\n===== RESEARCH REPORT =====\n")
    print(json.dumps(report, indent=4))
