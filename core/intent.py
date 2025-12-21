def is_summary_intent(prompt: str) -> bool:
    keywords = [
        "summary", "summarise", "overview",
        "main points", "key concepts",
        "what is covered", "topics", "explain lecture"
    ]

    if any(k in prompt.lower() for k in keywords):
        return True

    # heuristic fallback
    return len(prompt.split()) <= 12
