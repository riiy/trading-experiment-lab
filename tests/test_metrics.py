from texperiment.metrics.performance import profit_factor, win_rate
from texperiment.metrics.robustness import best_n_removed_mean
from texperiment.metrics.top_contribution import top_n_contribution_ratio


def test_performance_metrics():
    returns = [0.1, -0.05, 0.02, -0.01]
    assert round(profit_factor(returns), 4) == 2.0
    assert win_rate(returns) == 0.5


def test_robustness_metrics():
    returns = [0.3, 0.2, 0.1, -0.1]
    assert round(best_n_removed_mean(returns, n=1), 6) == round((0.2 + 0.1 - 0.1) / 3, 6)
    assert top_n_contribution_ratio(returns, n=1) == 0.3 / 0.5
