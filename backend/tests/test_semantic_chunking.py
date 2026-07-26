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


def test_semantic_chunker_keeps_two_same_topic_sentences_as_one_chunk():
    # Regression test: `distance >= threshold` alone guarantees at least one
    # split always happens (the value AT the percentile index is itself always
    # >= threshold) — with exactly one pairwise distance, that single distance
    # IS the threshold, so the old logic force-split every 2-sentence input
    # regardless of how related the two sentences actually are.
    chunker = SemanticChunker()
    text = (
        "Neurons are the basic building blocks of the brain. "
        "Each neuron connects to thousands of other neurons via synapses."
    )
    chunks = chunker.split(text)
    assert len(chunks) == 1


def test_semantic_chunker_does_not_shred_a_long_uniform_topic_text():
    # A single coherent topic across many sentences should NOT be shredded into
    # one chunk per sentence just because the percentile-threshold math always
    # finds *some* boundary to call a "breakpoint" when similarities are all
    # roughly uniform.
    chunker = SemanticChunker()
    text = (
        "Neurons are the basic building blocks of the nervous system. "
        "Each neuron has a cell body, dendrites, and an axon. "
        "Dendrites receive signals from neighboring neurons. "
        "The axon transmits electrical impulses to other neurons. "
        "Synapses are the junctions where neurons communicate with each other. "
        "Neurotransmitters carry signals across the synaptic gap between neurons."
    )
    sentence_count = len(split_sentences(text))
    chunks = chunker.split(text)
    assert len(chunks) < sentence_count  # not one chunk per sentence
