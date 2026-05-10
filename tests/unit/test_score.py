from egobench.eval.score import compute_scores


def test_compute_scores_raw_weighted_and_category():
    summary = compute_scores(
        [
            {"score": 10, "cluster_size": 3, "category": "Code"},
            {"score": 4, "cluster_size": 1, "category": "Writing"},
        ]
    )
    assert summary.raw == 7
    assert summary.frequency_weighted == 8.5
    assert summary.per_category == {"Code": 10, "Writing": 4}

