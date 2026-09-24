from itertools import pairwise

from agents.sql_analyst import sql_analyst


def _edges():
    graph = sql_analyst.get_graph()
    return {(e.source, e.target) for e in graph.edges}


def test_nodes_match_the_agreed_pipeline():
    nodes = set(sql_analyst.get_graph().nodes) - {"__start__", "__end__"}

    assert nodes == {
        "prompt_query_context", "generate_sql", "check_readonly", "canceled_sql",
        "validate_sql", "execute_sql", "report_failure", "represent_final_answer",
    }


def test_the_happy_path_is_in_order():
    edges = _edges()
    path = ["__start__", "prompt_query_context", "generate_sql", "check_readonly",
            "validate_sql", "execute_sql", "represent_final_answer", "__end__"]

    for step in pairwise(path):
        assert step in edges, step


def test_both_validation_and_execution_can_retry():
    edges = _edges()

    assert ("validate_sql", "generate_sql") in edges
    assert ("execute_sql", "generate_sql") in edges


def test_a_refused_query_ends_without_reaching_the_database():
    edges = _edges()

    assert ("check_readonly", "canceled_sql") in edges
    assert ("canceled_sql", "__end__") in edges
