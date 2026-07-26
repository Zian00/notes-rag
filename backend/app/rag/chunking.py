import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.semantic_chunking import SemanticChunker
from app.rag.types import Chunk, ParsedDocument

# tiktoken is only an approximation of Gemini's tokenizer; we use it solely to size
# chunks consistently (exactness is not required).
_ENCODING = "cl100k_base"


class Chunker:
    """Splits a ParsedDocument into chunks, WITHIN each segment (never merging
    across slides/pages/headings). For each segment:
      1. If it already fits in chunk_tokens, keep it as ONE chunk (no splitting).
      2. If it's too large and a SemanticChunker is configured, split it there
         first (meaning-based boundaries), then...
      3. ...anything still too large after that (or if no SemanticChunker is
         configured at all) falls back to fixed-size recursive splitting, same
         as this class's original behavior — the last-resort safety net.
    """

    def __init__(
        self,
        chunk_tokens: int,
        chunk_overlap_tokens: int,
        semantic_chunker: SemanticChunker | None = None,
    ) -> None:
        self._encoder = tiktoken.get_encoding(_ENCODING)
        self._chunk_tokens = chunk_tokens
        # "Recursive" = try to break on natural boundaries (paragraph → sentence → word)
        # before hard-cutting. from_tiktoken_encoder makes chunk_size/overlap count TOKENS,
        # not characters. Overlap repeats a few tokens between neighbours so context isn't
        # lost at a chunk boundary.
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=_ENCODING,
            chunk_size=chunk_tokens,
            chunk_overlap=chunk_overlap_tokens,
        )
        # Optional meaning-based splitter tried before the fixed-size fallback.
        # None preserves the old fixed-size-only behavior for callers not yet wired.
        self._semantic = semantic_chunker

    def split(self, document: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0  # chunk_index is global across the whole document, not per-segment
        # Split WITHIN each segment so a chunk never spans two slides/pages/headings —
        # that keeps each chunk topically coherent and lets us tag it with the right
        # page_number/section for citations later.
        for segment in document.segments:
            if not segment.text.strip():
                continue  # skip empty segments (e.g. a slide with no text)
            for piece in self._pieces_for_segment(segment.text):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    Chunk(
                        content=piece,
                        chunk_index=index,
                        # inherit the segment's location so the chunk stays traceable
                        page_number=segment.page_number,
                        section=segment.section,
                        token_count=len(self._encoder.encode(piece)),
                    )
                )
                index += 1
        return chunks

    def _pieces_for_segment(self, text: str) -> list[str]:
        # Fits already: no forced splitting — the segment becomes exactly one chunk.
        if len(self._encoder.encode(text)) <= self._chunk_tokens:
            return [text]

        # No semantic chunker configured: go straight to the fixed-size fallback.
        if self._semantic is None:
            return self._splitter.split_text(text)

        # Try meaning-based boundaries first; only pieces still too large after
        # that fall back to fixed-size splitting (the last-resort safety net).
        pieces: list[str] = []
        for piece in self._semantic.split(text):
            if len(self._encoder.encode(piece)) <= self._chunk_tokens:
                pieces.append(piece)
            else:
                pieces.extend(self._splitter.split_text(piece))
        return pieces
