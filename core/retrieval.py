from langchain_core.documents import Document


def retrieve_for_summary(db, lecture_id: str):

    # Get ALL chunks from this lecture
    # The metadata filter ensures we only get chunks from lecture_3
    all_chunks = db.get(
        where={"lecture": lecture_id}
    )

    # Convert to Document objects for the chain
    docs = [
        Document(page_content=text, metadata=meta)
        for text, meta in zip(all_chunks['documents'], all_chunks['metadatas'])
    ]

    return docs


def retrieve_for_qa(db, prompt: str):
    """
    Q&A uses semantic search
    """
    return db.similarity_search(prompt, k=3)
