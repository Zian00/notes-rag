import math

from app.rag.embeddings import l2_normalize


def test_l2_normalize_unit_length():
    out = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, rel_tol=1e-6)
    assert math.isclose(out[0], 0.6, rel_tol=1e-6)


def test_l2_normalize_zero_vector_is_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]
