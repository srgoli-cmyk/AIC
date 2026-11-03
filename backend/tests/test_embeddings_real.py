"""Uses the real model (downloaded on first use). Run with: pytest -m slow"""

import math

import pytest

from app import embeddings


@pytest.mark.slow
def test_real_model_gives_normalized_384_dim_vectors_and_sensible_similarity():
    a, b, c = embeddings.embed(
        [
            "The product rule gives the derivative of a product of two functions.",
            "How do we differentiate a product of functions?",
            "The cafeteria serves pasta on Fridays.",
        ]
    )
    assert len(a) == 384
    assert math.isclose(sum(x * x for x in a), 1.0, abs_tol=1e-3)
    dot = lambda u, v: sum(x * y for x, y in zip(u, v, strict=True))  # noqa: E731
    assert dot(a, b) > dot(a, c)
