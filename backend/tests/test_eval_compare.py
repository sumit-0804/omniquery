from decimal import Decimal

from evals.run_eval import results_match


def r(columns, rows):
    return (columns, rows)


def test_identical_results_match():
    assert results_match(r(["n"], [(7000,)]), r(["count"], [(7000,)]))


def test_extra_agent_columns_and_column_order_are_allowed():
    gold = r(["city", "n"], [("Ottawa", 905), ("Halifax", 890)])
    agent = r(["n", "label", "city"], [(890, "x", "Halifax"), (905, "y", "Ottawa")])
    assert results_match(gold, agent)


def test_numbers_compare_at_the_coarser_precision():
    assert results_match(r(["p"], [(Decimal("2.8"),)]), r(["p"], [(2.7624,)]))
    assert results_match(r(["a"], [(Decimal("64.5745032041311516"),)]), r(["a"], [(Decimal("64.57"),)]))


def test_wrong_numbers_do_not_match():
    assert not results_match(r(["p"], [(Decimal("2.8"),)]), r(["p"], [(2.5,)]))
    assert not results_match(r(["n"], [(1925,)]), r(["n"], [(1924,)]))


def test_rows_must_pair_correctly_not_just_columns():
    gold = r(["city", "n"], [("Ottawa", 1), ("Halifax", 2)])
    swapped = r(["city", "n"], [("Ottawa", 2), ("Halifax", 1)])
    assert not results_match(gold, swapped)


def test_different_row_counts_do_not_match():
    assert not results_match(r(["n"], [(1,), (2,)]), r(["n"], [(1,)]))


def test_empty_agent_result_fails_against_real_gold():
    assert not results_match(r(["n"], [(1925,)]), r(["n"], []))


def test_missing_gold_column_fails():
    gold = r(["city", "n"], [("Ottawa", 905)])
    agent = r(["n"], [(905,)])
    assert not results_match(gold, agent)
