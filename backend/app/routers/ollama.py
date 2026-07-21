from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from services.ollama_service import (
    get_ollama_models,
    get_ollama_status,
    chat_with_ollama,
)

router = APIRouter(
    prefix="/api/ollama",
    tags=["Ollama"],
)


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False


@router.get("/status")
def ollama_status() -> dict[str, Any]:
    return get_ollama_status()


@router.get("/models")
def ollama_models() -> dict[str, Any]:
    return get_ollama_models()


@router.post("/chat")
def ollama_chat(request: ChatRequest):
    return chat_with_ollama(
        model=request.model,
        messages=request.messages,
        stream=request.stream,
    )
