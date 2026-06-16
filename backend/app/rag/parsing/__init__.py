# Thin package facade: re-export the public API so callers can do
# `from app.rag.parsing import ParserDispatcher` while the logic lives in dispatcher.py.
from app.rag.parsing.dispatcher import ParserDispatcher, UnsupportedContentType

__all__ = ["ParserDispatcher", "UnsupportedContentType"]
