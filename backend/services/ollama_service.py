from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
).rstrip("/")

REQUEST_TIMEOUT = 5


def _get_json(path: str) -> dict[str, Any]:
    request = Request(
        url=f"{OLLAMA_BASE_URL}{path}",
        method="GET",
        headers={"Accept": "application/json"},
    )

    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        raw_data = response.read().decode("utf-8")
        return json.loads(raw_data)


def get_ollama_status() -> dict[str, Any]:
    try:
        version_data = _get_json("/api/version")

        return {
            "status": "online",
            "connected": True,
            "base_url": OLLAMA_BASE_URL,
            "version": version_data.get("version", "unknown"),
        }

    except HTTPError as error:
        return {
            "status": "offline",
            "connected": False,
            "base_url": OLLAMA_BASE_URL,
            "version": None,
            "error": f"Ollama returned HTTP {error.code}",
        }

    except URLError as error:
        return {
            "status": "offline",
            "connected": False,
            "base_url": OLLAMA_BASE_URL,
            "version": None,
            "error": str(error.reason),
        }

    except (TimeoutError, json.JSONDecodeError, OSError) as error:
        return {
            "status": "offline",
            "connected": False,
            "base_url": OLLAMA_BASE_URL,
            "version": None,
            "error": str(error),
        }


def get_ollama_models() -> dict[str, Any]:
    try:
        model_data = _get_json("/api/tags")
        raw_models = model_data.get("models", [])

        models: list[dict[str, Any]] = []

        for model in raw_models:
            details = model.get("details") or {}

            models.append(
                {
                    "name": model.get("name", "unknown"),
                    "model": model.get("model", model.get("name", "unknown")),
                    "size": model.get("size", 0),
                    "digest": model.get("digest", ""),
                    "modified_at": model.get("modified_at"),
                    "format": details.get("format"),
                    "family": details.get("family"),
                    "parameter_size": details.get("parameter_size"),
                    "quantization_level": details.get("quantization_level"),
                }
            )

        return {
            "status": "online",
            "connected": True,
            "count": len(models),
            "models": models,
        }

    except HTTPError as error:
        return {
            "status": "offline",
            "connected": False,
            "count": 0,
            "models": [],
            "error": f"Ollama returned HTTP {error.code}",
        }

    except URLError as error:
        return {
            "status": "offline",
            "connected": False,
            "count": 0,
            "models": [],
            "error": str(error.reason),
        }

    except (TimeoutError, json.JSONDecodeError, OSError) as error:
        return {
            "status": "offline",
            "connected": False,
            "count": 0,
            "models": [],
            "error": str(error),
        }
import urllib.request


def chat_with_ollama(
    model: str,
    messages: list[dict],
    stream: bool = False,
    *,
    think: bool | None = None,
    options: dict[str, Any] | None = None,
    timeout: float = 120,
):
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }

    if think is not None:
        request_payload["think"] = think

    if options:
        request_payload["options"] = options

    payload = json.dumps(request_payload).encode("utf-8")

    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    except HTTPError as error:
        return {
            "status": "error",
            "error": error.read().decode("utf-8"),
        }

    except URLError as error:
        return {
            "status": "error",
            "error": str(error.reason),
        }
