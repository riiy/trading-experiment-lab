"""Fail-closed raw/qfq mapping for prices displayed at a finite tick size.

The solver never changes stored prices.  It records the set of affine and/or
ratio mappings compatible with displayed-price intervals.  A canonical point
is only a deterministic serialization aid; execution must be evaluated over
the complete set through :func:`evaluate_mapping_branch_invariance`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite, nextafter
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np


PASS_EXACT_AFFINE = "PASS_EXACT_AFFINE"
PASS_EXACT_DAILY_RATIO = "PASS_EXACT_DAILY_RATIO"
PASS_ROUNDING_INTERVAL_IDENTIFIED = "PASS_ROUNDING_INTERVAL_IDENTIFIED"
PASS_ROUNDING_INTERVAL_SET = "PASS_ROUNDING_INTERVAL_SET"
PASS_MAPPING_BRANCH_INVARIANT = "PASS_MAPPING_BRANCH_INVARIANT"
NOT_EVALUABLE_MAPPING_AMBIGUITY = "NOT_EVALUABLE_MAPPING_AMBIGUITY"
SOURCE_MAPPING_INCONSISTENT = "SOURCE_MAPPING_INCONSISTENT"

_REPRESENTATIVE_METHOD = "SLOPE_INTERVAL_MIDPOINT_INTERCEPT_SLICE_MIDPOINT_V1"


@dataclass(frozen=True)
class PriceInterval:
    """A conservative raw-price range induced by all feasible parameters."""

    lower: float
    upper: float


@dataclass(frozen=True)
class MappingFeasibleSet:
    """One rounding-compatible mapping family.

    The slope interval is open at both finite endpoints because the displayed
    price intervals are half-open.  ``intercept_interval`` is the slice at the
    canonical slope, not a claim that slope and intercept vary independently.
    """

    model: str
    slope_interval: tuple[float, float]
    intercept_interval: tuple[float, float]
    canonical_slope: float
    canonical_intercept: float
    parameter_set_dimension: int
    identified: bool
    quote_tick: float
    raw_prices: tuple[float, ...]
    qfq_prices: tuple[float, ...]
    representative_method: str = _REPRESENTATIVE_METHOD

    @property
    def affine_feasible(self) -> bool:
        return self.model == "AFFINE"

    @property
    def ratio_feasible(self) -> bool:
        return self.model == "RATIO"

    def raw_price_interval(self, qfq_price: float) -> PriceInterval:
        """Return a conservative raw-price interval for a qfq structure price."""
        if not isfinite(qfq_price) or qfq_price <= 0:
            raise ValueError("qfq_price must be finite and positive")
        slope_low, slope_high = self.slope_interval
        if self.model == "RATIO":
            if not isfinite(slope_high):
                return PriceInterval(0.0, inf)
            return PriceInterval(qfq_price / slope_high, qfq_price / slope_low)

        # An unbounded affine slope admits parameter choices outside any finite
        # execution-price enclosure. Treat it as maximally ambiguous instead
        # of sampling the canonical representative.
        if not isfinite(slope_high):
            return PriceInterval(0.0, inf)

        points = _affine_slope_candidates(self)
        lower_values: list[float] = []
        upper_values: list[float] = []
        for slope in points:
            intercept_low, intercept_high = _affine_intercept_slice(
                slope, self.raw_prices, self.qfq_prices, self.quote_tick
            )
            if intercept_low >= intercept_high:
                continue
            # Any b in the open interval is possible. Include endpoints as a
            # conservative closure so equality at an execution boundary blocks.
            lower_values.append((qfq_price - intercept_high) / slope)
            upper_values.append((qfq_price - intercept_low) / slope)
        if not lower_values:
            return PriceInterval(0.0, inf)
        return PriceInterval(min(lower_values), max(upper_values))


@dataclass(frozen=True)
class RoundingIntervalMapping:
    """All feasible models for one displayed raw/qfq OHLC row."""

    affine: MappingFeasibleSet | None
    ratio: MappingFeasibleSet | None
    status: str

    @property
    def feasible_sets(self) -> tuple[MappingFeasibleSet, ...]:
        return tuple(item for item in (self.affine, self.ratio) if item is not None)


@dataclass(frozen=True)
class MappingOutcome:
    status: str
    material_outcome_variants: int
    raw_price_ranges: Mapping[str, PriceInterval]
    outcomes: tuple[str, ...]


def solve_rounding_interval_mapping(
    raw_ohlc: Sequence[float],
    qfq_ohlc: Sequence[float],
    *,
    quote_tick: float = 0.01,
    allow_ratio: bool = True,
) -> RoundingIntervalMapping:
    """Solve mapping sets consistent with half-open displayed-price intervals.

    Each displayed price ``p`` represents ``[p-tick/2, p+tick/2)``.  The
    affine model permits ``qfq = slope * raw + intercept`` with positive slope.
    The ratio model fixes the intercept to zero.  Neither solver changes the
    displayed prices or broadens the existing exact-mapping tolerance.
    """
    raw = _positive_prices(raw_ohlc, "raw_ohlc")
    qfq = _positive_prices(qfq_ohlc, "qfq_ohlc")
    if len(raw) != len(qfq):
        raise ValueError("raw_ohlc and qfq_ohlc must have equal length")
    if quote_tick <= 0 or not isfinite(quote_tick):
        raise ValueError("quote_tick must be finite and positive")

    affine = _solve_affine(raw, qfq, quote_tick)
    ratio = _solve_ratio(raw, qfq, quote_tick) if allow_ratio else None
    feasible = tuple(item for item in (affine, ratio) if item is not None)
    if not feasible:
        status = SOURCE_MAPPING_INCONSISTENT
    elif all(item.identified for item in feasible):
        status = PASS_ROUNDING_INTERVAL_IDENTIFIED
    else:
        status = PASS_ROUNDING_INTERVAL_SET
    return RoundingIntervalMapping(affine=affine, ratio=ratio, status=status)


def evaluate_mapping_branch_invariance(
    mapping: RoundingIntervalMapping,
    qfq_structure_prices: Mapping[str, float],
    outcome_evaluator: Callable[[Mapping[str, PriceInterval]], Iterable[str]],
) -> MappingOutcome:
    """Ask an execution-aware caller whether all feasible branches agree.

    ``outcome_evaluator`` receives conservative raw-price ranges for every
    structure price it needs (entry, stop, target, or exit).  It must return
    every materially distinct outcome still possible in those ranges.  The
    function never infers an outcome from the canonical representative.
    """
    if not mapping.feasible_sets:
        return MappingOutcome(
            status=SOURCE_MAPPING_INCONSISTENT,
            material_outcome_variants=0,
            raw_price_ranges={},
            outcomes=(),
        )
    ranges: dict[str, PriceInterval] = {}
    for name, qfq_price in qfq_structure_prices.items():
        intervals = [feasible.raw_price_interval(float(qfq_price)) for feasible in mapping.feasible_sets]
        ranges[name] = PriceInterval(
            lower=min(interval.lower for interval in intervals),
            upper=max(interval.upper for interval in intervals),
        )
    outcomes = tuple(sorted(set(str(item) for item in outcome_evaluator(ranges))))
    if len(outcomes) == 1:
        status = PASS_MAPPING_BRANCH_INVARIANT
    else:
        status = NOT_EVALUABLE_MAPPING_AMBIGUITY
    return MappingOutcome(
        status=status,
        material_outcome_variants=len(outcomes),
        raw_price_ranges=ranges,
        outcomes=outcomes,
    )


def _solve_ratio(raw: tuple[float, ...], qfq: tuple[float, ...], tick: float) -> MappingFeasibleSet | None:
    half_tick = tick / 2
    slope_low = max((adjusted - half_tick) / (source + half_tick) for source, adjusted in zip(raw, qfq))
    slope_high = min((adjusted + half_tick) / (source - half_tick) for source, adjusted in zip(raw, qfq))
    slope_low = max(slope_low, 0.0)
    if slope_low >= slope_high:
        return None
    canonical = (slope_low + slope_high) / 2
    return MappingFeasibleSet(
        model="RATIO",
        slope_interval=(slope_low, slope_high),
        intercept_interval=(0.0, 0.0),
        canonical_slope=canonical,
        canonical_intercept=0.0,
        parameter_set_dimension=1,
        identified=False,
        quote_tick=tick,
        raw_prices=raw,
        qfq_prices=qfq,
    )


def _solve_affine(raw: tuple[float, ...], qfq: tuple[float, ...], tick: float) -> MappingFeasibleSet | None:
    half_tick = tick / 2
    raw_low = [value - half_tick for value in raw]
    raw_high = [value + half_tick for value in raw]
    qfq_low = [value - half_tick for value in qfq]
    qfq_high = [value + half_tick for value in qfq]
    slope_low, slope_high = 0.0, inf
    # b must satisfy q_low - a*raw_high < b < q_high - a*raw_low.
    for left_low, left_high in zip(qfq_low, raw_high):
        for right_high, right_low in zip(qfq_high, raw_low):
            coefficient = right_low - left_high
            rhs = right_high - left_low
            if coefficient > 0:
                slope_high = min(slope_high, rhs / coefficient)
            elif coefficient < 0:
                slope_low = max(slope_low, rhs / coefficient)
            elif rhs <= 0:
                return None
    slope_low = max(slope_low, 0.0)
    if slope_low >= slope_high:
        return None
    canonical_slope = _interior_slope(slope_low, slope_high)
    intercept_low, intercept_high = _affine_intercept_slice(canonical_slope, raw, qfq, tick)
    if intercept_low >= intercept_high:
        return None
    return MappingFeasibleSet(
        model="AFFINE",
        slope_interval=(slope_low, slope_high),
        intercept_interval=(intercept_low, intercept_high),
        canonical_slope=canonical_slope,
        canonical_intercept=(intercept_low + intercept_high) / 2,
        parameter_set_dimension=2,
        identified=False,
        quote_tick=tick,
        raw_prices=raw,
        qfq_prices=qfq,
    )


def _affine_intercept_slice(
    slope: float, raw: Sequence[float], qfq: Sequence[float], tick: float
) -> tuple[float, float]:
    half_tick = tick / 2
    lower = max(adjusted - half_tick - slope * (source + half_tick) for source, adjusted in zip(raw, qfq))
    upper = min(adjusted + half_tick - slope * (source - half_tick) for source, adjusted in zip(raw, qfq))
    return lower, upper


def _affine_slope_candidates(feasible: MappingFeasibleSet) -> tuple[float, ...]:
    low, high = feasible.slope_interval
    candidates = {_interior_slope(low, high), feasible.canonical_slope}
    half_tick = feasible.quote_tick / 2
    lower_lines = [(adjusted - half_tick, source + half_tick) for source, adjusted in zip(feasible.raw_prices, feasible.qfq_prices)]
    upper_lines = [(adjusted + half_tick, source - half_tick) for source, adjusted in zip(feasible.raw_prices, feasible.qfq_prices)]
    for lines in (lower_lines, upper_lines):
        for constant_a, slope_a in lines:
            for constant_b, slope_b in lines:
                denominator = slope_a - slope_b
                if denominator == 0:
                    continue
                point = (constant_a - constant_b) / denominator
                if low < point < high:
                    candidates.add(point)
    if isfinite(high):
        candidates.add(nextafter(high, low))
    candidates.add(nextafter(low, inf))
    return tuple(sorted(point for point in candidates if point > 0 and (not isfinite(high) or point < high)))


def _interior_slope(lower: float, upper: float) -> float:
    if isfinite(upper):
        return (lower + upper) / 2
    return max(1.0, lower * 2 if lower else 1.0)


def _positive_prices(values: Sequence[float], name: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != 4:
        raise ValueError(f"{name} must contain exactly four OHLC values")
    if any(not isfinite(value) or value <= 0 for value in result):
        raise ValueError(f"{name} must contain finite positive prices")
    return result
