import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.types import Chunk, ParsedDocument

# tiktoken is only an approximation of Gemini's tokenizer; we use it solely to size
# chunks consistently (exactness is not required).
_ENCODING = "cl100k_base"


class Chunker:
    """Splits a ParsedDocument into chunks, recursively by token count, WITHIN each
    segment (never merging across slides/pages). Each chunk inherits its segment's
    page_number/section metadata."""

    def __init__(self, chunk_tokens: int, chunk_overlap_tokens: int) -> None:
        self._encoder = tiktoken.get_encoding(_ENCODING)
        self._splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=_ENCODING,
            chunk_size=chunk_tokens,
            chunk_overlap=chunk_overlap_tokens,
        )

    def split(self, document: ParsedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0
        for segment in document.segments:
            if not segment.text.strip():
                continue
            for piece in self._splitter.split_text(segment.text):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    Chunk(
                        content=piece,
                        chunk_index=index,
                        page_number=segment.page_number,
                        section=segment.section,
                        token_count=len(self._encoder.encode(piece)),
                    )
                )
                index += 1
        return chunks
