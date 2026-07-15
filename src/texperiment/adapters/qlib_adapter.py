from __future__ import annotations


class QlibAdapter:
    """Optional adapter for future Qlib integration.

    STOCK_RS_PULLBACK_v1 does not depend on Qlib. This placeholder keeps the
    project open to later workflows such as multi-factor research, model
    training, experiment recording, and portfolio backtesting.
    """

    def __init__(self) -> None:
        self.enabled = False

    def require_qlib(self) -> None:
        try:
            import qlib  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Install optional dependency: pip install -e '.[qlib]'") from exc
