from __future__ import annotations

import pytest
import pandas as pd

from texperiment.data.rounding_interval_mapping import (
    NOT_EVALUABLE_MAPPING_AMBIGUITY,
    PASS_MAPPING_BRANCH_INVARIANT,
    PASS_ROUNDING_INTERVAL_SET,
    SOURCE_MAPPING_INCONSISTENT,
    evaluate_mapping_branch_invariance,
    solve_rounding_interval_mapping,
)
from texperiment.data.tdx_paired_source import fit_raw_qfq_mapping


def test_affine_and_ratio_interval_case_has_deterministic_representatives():
    raw = [6.60, 6.62, 6.57, 6.59]
    qfq = [0.73, 0.73, 0.73, 0.73]

    first = solve_rounding_interval_mapping(raw, qfq)
    second = solve_rounding_interval_mapping(raw, qfq)

    assert first.status == PASS_ROUNDING_INTERVAL_SET
    assert first.affine is not None
    assert first.ratio is not None
    assert first.affine.canonical_slope == pytest.approx(second.affine.canonical_slope)
    assert first.affine.canonical_intercept == pytest.approx(second.affine.canonical_intercept)
    assert first.affine.parameter_set_dimension == 2
    assert first.ratio.parameter_set_dimension == 1


def test_affine_only_degenerate_qfq_case():
    mapping = solve_rounding_interval_mapping(
        [6.12, 6.18, 6.11, 6.17],
        [3.99, 3.99, 3.99, 3.99],
    )

    assert mapping.affine is not None
    assert mapping.ratio is None


def test_ratio_feasible_degenerate_qfq_case_preserves_affine_ambiguity():
    mapping = solve_rounding_interval_mapping(
        [10.87, 10.88, 10.87, 10.88],
        [7.34, 7.34, 7.34, 7.34],
    )

    assert mapping.affine is not None
    assert mapping.ratio is not None
    assert mapping.affine.slope_interval[1] == float("inf")


def test_half_open_tick_intervals_do_not_treat_touching_bounds_as_feasible():
    mapping = solve_rounding_interval_mapping(
        [1.00, 2.00, 3.00, 4.00],
        [1.00, 2.00, 1.00, 2.00],
    )

    assert mapping.status == SOURCE_MAPPING_INCONSISTENT


def test_branch_invariance_uses_full_feasible_set_not_canonical_point():
    mapping = solve_rounding_interval_mapping(
        [6.60, 6.62, 6.57, 6.59],
        [0.73, 0.73, 0.73, 0.73],
    )

    invariant = evaluate_mapping_branch_invariance(
        mapping,
        {"stop": 0.70, "target": 0.80},
        lambda ranges: ["D5_EXIT"] if ranges["stop"].upper < ranges["target"].upper else ["D5_EXIT"],
    )
    ambiguous = evaluate_mapping_branch_invariance(
        mapping,
        {"stop": 0.70},
        lambda _ranges: ["STOP", "TARGET"],
    )

    assert invariant.status == PASS_MAPPING_BRANCH_INVARIANT
    assert invariant.material_outcome_variants == 1
    assert ambiguous.status == NOT_EVALUABLE_MAPPING_AMBIGUITY
    assert ambiguous.material_outcome_variants == 2


def test_mapping_is_code_agnostic_and_prices_are_not_transformed():
    mapping = solve_rounding_interval_mapping(
        [10.00, 10.02, 9.99, 10.01],
        [0.42, 0.42, 0.42, 0.42],
    )

    assert mapping.feasible_sets
    for feasible in mapping.feasible_sets:
        assert feasible.raw_prices == (10.0, 10.02, 9.99, 10.01)
        assert feasible.qfq_prices == (0.42, 0.42, 0.42, 0.42)


def test_unbounded_affine_set_is_conservatively_ambiguous_for_execution():
    mapping = solve_rounding_interval_mapping(
        [10.87, 10.88, 10.87, 10.88],
        [7.34, 7.34, 7.34, 7.34],
    )

    outcome = evaluate_mapping_branch_invariance(
        mapping,
        {"entry": 7.34},
        lambda ranges: ["AMBIGUOUS"] if ranges["entry"].upper == float("inf") else ["FIXED"],
    )

    assert outcome.raw_price_ranges["entry"].lower == 0.0
    assert outcome.raw_price_ranges["entry"].upper == float("inf")
    assert outcome.status == PASS_MAPPING_BRANCH_INVARIANT


def test_existing_exact_mapping_is_not_changed_or_called_by_interval_solver():
    frame = pd.DataFrame(
        {
            "raw_open": [10.0], "raw_high": [11.0], "raw_low": [9.0], "raw_close": [10.5],
            "adj_open": [6.0], "adj_high": [6.5], "adj_low": [5.5], "adj_close": [6.25],
        }
    )

    exact = fit_raw_qfq_mapping(frame)

    assert exact.loc[0, "adjustment_status"] == "KNOWN_AFFINE_RAW_QFQ_VALIDATED"
    assert exact.loc[0, "adj_factor"] == pytest.approx(0.5)
    assert exact.loc[0, "adj_offset"] == pytest.approx(1.0)
