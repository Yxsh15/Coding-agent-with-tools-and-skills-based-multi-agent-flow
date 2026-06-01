from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from uuid import uuid4


EventEmitter = Callable[[str, dict[str, object]], Awaitable[None]]


def iter_text_chunks(content: str, chunk_size: int = 32) -> Iterator[str]:
    if not content:
        return
    for index in range(0, len(content), chunk_size):
        yield content[index : index + chunk_size]


async def stream_message(
    emit: EventEmitter,
    agent: str,
    role: str,
    content: str,
    *,
    chunk_size: int = 32,
    delay_ms: int = 18,
) -> str:
    message_id = uuid4().hex
    streamed = ""

    for chunk in iter_text_chunks(content, chunk_size):
        streamed += chunk
        await emit(
            "message_chunk",
            {
                "message_id": message_id,
                "agent": agent,
                "role": role,
                "delta": chunk,
                "content": streamed,
            },
        )
        if delay_ms > 0 and len(streamed) < len(content):
            await asyncio.sleep(delay_ms / 1000)

    await emit(
        "message",
        {
            "message_id": message_id,
            "agent": agent,
            "role": role,
            "content": content,
        },
    )
    return message_id
