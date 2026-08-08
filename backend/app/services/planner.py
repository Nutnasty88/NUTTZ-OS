import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen3:8b"


def generate_plan(mission: str):
    prompt = f"""
You are the Planner Agent inside NUTTZ OS.

Your job is to create a concise execution plan.

Mission:
{mission}

Return ONLY a numbered list.

Example:

1. Analyze requirements
2. Research dependencies
3. Design architecture
4. Build backend
5. Build frontend
6. Write tests
7. Generate documentation
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
        },
        timeout=300,
    )

    response.raise_for_status()

    return response.json()["response"].strip()


if __name__ == "__main__":
    print(generate_plan("Build a local password manager"))
