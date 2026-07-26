import re

from fastembed import TextEmbedding

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticChunker:
    """Splits a structure-less block of text into sub-pieces at points where the
    meaning shifts, rather than at a fixed size. Used only as a FALLBACK — see
    Chunker._pieces_for_segment — when a segment is too large and has no finer
    heading/slide/paragraph structure to split on.

    Uses local sentence embeddings (fastembed, ONNX — no PyTorch, no network call
    per request) purely to find break points; the final chunk embeddings used for
    search are still produced by GeminiEmbeddingsProvider elsewhere in the pipeline.
    """

    def __init__(
        self, model_name: str = "BAAI/bge-small-en-v1.5", breakpoint_percentile: float = 85.0
    ) -> None:
        self._model = TextEmbedding(model_name=model_name)
        self._percentile = breakpoint_percentile

    def split(self, text: str) -> list[str]:
        sentences = split_sentences(text)
        if len(sentences) <= 1:
            return [text] if text.strip() else []

        # fastembed's TextEmbedding.embed() returns an iterable of numpy arrays
        # (not plain lists as the type hint suggests) — convert to list[float]
        # so _cosine's arithmetic operates on plain Python floats.
        vectors = [vector.tolist() for vector in self._model.embed(sentences)]
        similarities = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
        # A breakpoint is a similarity LOW point. Convert to "distance" (1 - similarity)
        # and use a percentile threshold — the standard method for semantic chunking:
        # only the sharpest topic shifts (the top `100 - percentile`% of distances)
        # become chunk boundaries, everything else stays merged.
        distances = [1.0 - s for s in similarities]
        sorted_distances = sorted(distances)
        idx = min(int(len(sorted_distances) * self._percentile / 100), len(sorted_distances) - 1)
        threshold = sorted_distances[idx]

        # WHY this guard: `distance >= threshold` alone guarantees AT LEAST ONE
        # split always happens, because the value AT the percentile index is
        # itself always >= threshold. When a text is one solid, uniform-topic
        # block of prose, every consecutive-sentence distance is roughly the
        # same, so EVERY boundary then satisfies `>= threshold` too — shredding
        # the text into one chunk per sentence with no real topic shift
        # anywhere. Chosen fix: skip splitting entirely when the SPREAD between
        # the smallest and largest distance is below a small epsilon — that
        # directly tests "is there a genuinely sharper shift somewhere, or are
        # these all roughly the same distance apart", which a plain threshold
        # comparison can't distinguish (and which a multiplicative margin on
        # `threshold` alone would mishandle whenever the uniform value happens
        # to be near zero). A real topic boundary's distance towers over the
        # rest of the distribution, so genuinely different topics still split.
        _MIN_DISTANCE_SPREAD = 0.02
        spread = sorted_distances[-1] - sorted_distances[0]
        if spread < _MIN_DISTANCE_SPREAD:
            return [" ".join(sentences)]

        pieces: list[str] = []
        current = [sentences[0]]
        for i, distance in enumerate(distances):
            if distance >= threshold:
                pieces.append(" ".join(current))
                current = [sentences[i + 1]]
            else:
                current.append(sentences[i + 1])
        pieces.append(" ".join(current))
        return pieces
