from collections.abc import AsyncIterator
from typing import Any

from app.rag.embeddings import EmbeddingsProvider
from app.rag.ocr import OcrProvider
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from PIL import Image


class _StructuredRunnable:
    """Wraps FakeChatModel for with_structured_output — pops next queued object."""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    async def ainvoke(self, *_a: Any, **_k: Any) -> Any:
        return self._parent._next()  # next queued structured object (e.g. Grade)


class FakeChatModel(BaseChatModel):
    """Deterministic, scripted chat model for graph/service tests.

    Queue responses with ``FakeChatModel(responses=[...])``. Each entry is either an
    ``AIMessage`` (for agent/generate/rewrite calls) or a pydantic object (for
    ``with_structured_output`` grading). Responses are consumed in order.

    Why pydantic v2 private attribute via ``object.__setattr__``:
    BaseChatModel is a pydantic BaseModel; plain Python assignment to ``_idx``
    would be rejected. We store the index as a pydantic PrivateAttr but reset it
    through ``object.__setattr__`` so it survives across async awaits without
    triggering pydantic's field validation.
    """

    responses: list[Any] = []
    _idx: int = 0  # tracks which response to return next

    model_config = {"arbitrary_types_allowed": True}

    def _next(self) -> Any:
        r = self.responses[self._idx]
        object.__setattr__(self, "_idx", self._idx + 1)
        return r

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, *_a: Any, **_k: Any) -> "FakeChatModel":
        # Return self so the same scripted queue is used for tool-bound calls.
        return self

    def with_structured_output(self, *_a: Any, **_k: Any) -> _StructuredRunnable:
        return _StructuredRunnable(self)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = self._next()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = self._next()
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _astream(  # type: ignore[override]
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream the next queued message as a single chunk.

        _astream must yield ``ChatGenerationChunk`` (not ``ChatGeneration``) — the
        base class's ``astream`` wraps these into ``AIMessageChunk`` deltas. We yield
        one chunk with the full content; tests that check streaming see at least one delta.

        Why we copy tool_calls onto the chunk:
        When ChatService uses ``graph.astream(stream_mode="messages")``, LangGraph
        intercepts model.ainvoke() and calls _astream internally instead of _agenerate.
        If the chunk doesn't carry tool_calls, route_after_agent sees no tool_calls on the
        accumulated message and routes to generate directly — skipping the tools node.
        Copying tool_calls onto the AIMessageChunk preserves the routing behaviour.
        """
        from langchain_core.messages import AIMessage as _AI

        msg = self._next()
        if isinstance(msg, _AI) and msg.tool_calls:
            # Preserve tool_calls so LangGraph routes correctly after streaming.
            chunk = AIMessageChunk(content=msg.content, tool_calls=msg.tool_calls)
        else:
            text = msg.content if isinstance(msg, BaseMessage) else str(msg)
            chunk = AIMessageChunk(content=text)
        yield ChatGenerationChunk(message=chunk)


class FakeOcrProvider(OcrProvider):
    """Returns a fixed string, ignoring the image — deterministic, no Tesseract."""

    def __init__(self, text: str = "ocr text") -> None:
        self._text = text

    def extract_text(self, image: Image.Image) -> str:
        return self._text


class FakeEmbeddingsProvider(EmbeddingsProvider):
    """Deterministic unit vectors derived from text length — no network/key.

    Vector i is a one-hot at position (len(text) % dim), so different-length texts
    sort deterministically by cosine distance.
    """

    def __init__(self, dimension: int = 1536) -> None:
        self._dim = dimension

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        v[len(text) % self._dim] = 1.0
        return v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
