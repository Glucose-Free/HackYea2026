import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from gateway.api.dependencies import get_components, require_gateway_api_key
from gateway.components import GatewayComponents
from gateway.core.conversation import Conversation, Message
from gateway.core.reply import GatewayReply

MODEL_ID = "company-assistant"
MODEL_OWNER = "ai-control-gateway"
TEXT_PART_TYPE = "text"
TEXT_PART_SEPARATOR = "\n"
STREAM_CHUNK_CHARS = 24
SSE_DONE_LINE = "data: [DONE]\n\n"
SSE_MEDIA_TYPE = "text/event-stream"
MISSING_IDENTITY_DETAIL = "user identity headers are missing"
MISSING_USER_MESSAGE_DETAIL = "the request contains no non-empty user message"
FINISH_REASON_STOP = "stop"
ASSISTANT_ROLE = "assistant"

router = APIRouter(dependencies=[Depends(require_gateway_api_key)])


class ChatContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    text: str | None = None


class ChatMessageIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str | list[ChatContentPart] | None = None

    def get_text(self) -> str:
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return TEXT_PART_SEPARATOR.join(part.text for part in self.content if part.type == TEXT_PART_TYPE and part.text)


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = MODEL_ID
    messages: list[ChatMessageIn]
    stream: bool = False


@router.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": MODEL_OWNER}]}


@router.post("/v1/chat/completions")
async def create_chat_completion(
    body: ChatCompletionRequest,
    request: Request,
    components: GatewayComponents = Depends(get_components),
):
    user = components.identity_resolver.resolve(request.headers)
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, MISSING_IDENTITY_DETAIL)
    conversation = build_conversation(body)
    latest_user_message = conversation.get_latest_user_message()
    if latest_user_message is None or not latest_user_message.content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MISSING_USER_MESSAGE_DETAIL)
    reply = await components.gateway.handle(conversation, user)
    if body.stream:
        return StreamingResponse(build_stream_lines(reply), media_type=SSE_MEDIA_TYPE)
    return build_completion_body(reply)


def build_conversation(body: ChatCompletionRequest) -> Conversation:
    return Conversation(tuple(Message(message.role, message.get_text()) for message in body.messages))


def build_completion_body(reply: GatewayReply) -> dict[str, Any]:
    return {
        "id": reply.request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": {"role": ASSISTANT_ROLE, "content": reply.text}, "finish_reason": FINISH_REASON_STOP}],
    }


def build_stream_lines(reply: GatewayReply) -> Iterator[str]:
    created = int(time.time())
    deltas: list[tuple[dict[str, Any], str | None]] = [({"role": ASSISTANT_ROLE}, None)]
    deltas += [({"content": reply.text[start:start + STREAM_CHUNK_CHARS]}, None) for start in range(0, len(reply.text), STREAM_CHUNK_CHARS)]
    deltas.append(({}, FINISH_REASON_STOP))
    for delta, finish_reason in deltas:
        chunk = {
            "id": reply.request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL_ID,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    yield SSE_DONE_LINE
