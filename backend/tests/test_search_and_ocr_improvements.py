import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_processor import _calculate_match_quality, _estimate_text_similarity


def test_calculate_match_quality_prioritizes_phrase_similarity_over_single_word():
    target = "Cristo salvò col Suo prezioso sangue"
    phrase_query = "cristo salvo sangue"
    single_word_query = "sangue"

    phrase_quality = _calculate_match_quality(target, phrase_query)
    single_quality = _calculate_match_quality(target, single_word_query)

    assert phrase_quality >= 0.55
    assert phrase_quality > single_quality


def test_estimate_text_similarity_is_high_for_nearly_identical_phrases():
    text_a = "Cristo salvò col Suo prezioso sangue"
    text_b = "Cristo salvò col suo prezioso sangue"
    unrelated = "Dio mio ti benedica"

    assert _estimate_text_similarity(text_a, text_b) >= 0.9
    assert _estimate_text_similarity(text_a, unrelated) < 0.35


def test_typo_tolerant_ranking_still_prefers_phrase_like_queries():
    target = "Quando sei afflitto"
    typo_query = "qundo sei afflitto"
    single_word_query = "afflitto"

    typo_quality = _calculate_match_quality(target, typo_query)
    single_quality = _calculate_match_quality(target, single_word_query)

    assert typo_quality >= 0.8
    assert typo_quality > single_quality


def test_sanitize_snippet_for_api_drops_musical_noise():
    from pdf_processor import sanitize_snippet_for_api

    noisy = "& ? b b 26 œœ œ œœb œ chie - do se_il Si -"
    sanitized = sanitize_snippet_for_api(noisy)

    assert sanitized == ""
