"""
MLflow historical data import for regression analysis.

Populates a local directory with kpis.json files from previous MLflow runs,
enabling the caliper analyze step to perform regression detection.
"""

from __future__ import annotations

import logging
from pathlib import Path

from projects.core.library import env
from projects.mcp_gateway.postprocess.mcp_gateway.mlflow_history import write_historical_kpis
from projects.mcp_gateway.orchestration.notifications import KPI_TO_MLFLOW_METRIC

logger = logging.getLogger(__name__)


def populate_historical_data(max_runs: int = 10) -> None:
    """Download historical KPIs from MLflow for regression analysis.

    Writes kpis.json files into ARTIFACT_BASE_DIR/historical_data/ so that
    the caliper analyze step can find them via ../historical_data relative path.
    """
    try:
        output_dir = Path(env.ARTIFACT_BASE_DIR) / "historical_data"
        count = write_historical_kpis(
            output_dir=output_dir,
            kpi_to_mlflow_metric=KPI_TO_MLFLOW_METRIC,
            max_runs=max_runs,
        )
        if count:
            logger.info("Populated %d historical KPI entries for regression analysis", count)
        else:
            logger.info("No historical KPI data found in MLflow")
    except Exception as e:
        logger.warning("Failed to populate historical data (non-fatal): %s", type(e).__name__)
