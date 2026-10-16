from evals.retrieval import covers

INDEX = {"§62.25-20(a)", "§62.25-20(b)", "§62.30-1", "§62.35-50", "§62.35-50(c)", "매뉴얼 4.2"}


def test_exact():
    assert covers("매뉴얼 4.2", "매뉴얼 4.2", INDEX)
    assert not covers("매뉴얼 4.2", "매뉴얼 4.3", INDEX)


def test_paragraph_chunk_covers_its_subparagraph():
    assert covers("§62.25-20(b)", "§62.25-20(b)(2)", INDEX)


def test_whole_section_covers_paragraph_only_when_not_split():
    assert covers("§62.30-1", "§62.30-1(b)", INDEX)  # 문단으로 안 잘린 조
    assert not covers("§62.35-50", "§62.35-50(c)", INDEX)  # 잘린 조의 머리글 조각


def test_part_of_gold_section_counts():
    assert covers("§62.35-50(c)", "§62.35-50", INDEX)


def test_similar_numbers_do_not_match():
    assert not covers("§62.25-2", "§62.25-20(b)", INDEX)


def test_table_gold_can_require_a_specific_row():
    from evals.retrieval import hit_matches

    gold = {"loc": "매뉴얼 2.1", "contains": "ME_LO_PRESS_IN"}
    right = {"locator": "매뉴얼 2.1", "title": "2.1 설정값 표 — ME_LO_PRESS_IN", "text": ""}
    other_row = {"locator": "매뉴얼 2.1", "title": "2.1 설정값 표 — ME_FO_PRESS_IN", "text": ""}
    assert hit_matches(right, gold, INDEX)
    assert not hit_matches(other_row, gold, INDEX)
