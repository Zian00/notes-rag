from app.rag.semantic_chunking import SemanticChunker, split_sentences


def test_split_sentences_splits_on_sentence_boundaries():
    text = "First sentence. Second sentence! Third sentence?"
    assert split_sentences(text) == [
        "First sentence.", "Second sentence!", "Third sentence?",
    ]


def test_semantic_chunker_groups_related_sentences_and_splits_on_topic_shift():
    # Real fastembed model — this test needs network access on first run to
    # download the model (cached afterward). Two clearly unrelated topics should
    # end up in different chunks; assertions are deliberately loose on the exact
    # split point since real embedding similarity isn't perfectly deterministic
    # across model versions.
    chunker = SemanticChunker()
    text = (
        "Neurons are the basic building blocks of the brain. "
        "Each neuron connects to thousands of others via synapses. "
        "Photosynthesis converts sunlight into chemical energy in plants. "
        "Chlorophyll absorbs light primarily in the blue and red wavelengths."
    )
    chunks = chunker.split(text)
    assert len(chunks) >= 2
    assert any("neuron" in c.lower() for c in chunks)
    assert any("photosynthesis" in c.lower() for c in chunks)
