"""SQLAlchemy metadata & table definitions for MandiMitra."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON


def _json_type():
    return JSON().with_variant(JSONB(), "postgresql")


def build_metadata(use_schemas: bool = True) -> MetaData:
    """Build SQLAlchemy Core metadata; schema prefixes are active for PostgreSQL and omitted for Demo SQLite."""
    metadata = MetaData()
    s_stg = "staging" if use_schemas else None
    s_wh = "warehouse" if use_schemas else None
    s_op = "operational" if use_schemas else None
    s_an = "analytics" if use_schemas else None

    def fk(schema: str | None, table_col: str) -> str:
        return f"{schema}.{table_col}" if schema else table_col

    # Staging
    Table(
        "stg_agmarknet_prices",
        metadata,
        Column("stg_id", Integer, primary_key=True, autoincrement=True),
        Column("batch_id", Text, nullable=False),
        Column("source_name", Text, nullable=False),
        Column("raw_file_path", Text),
        Column("arrival_date", Date),
        Column("state", Text),
        Column("district", Text),
        Column("market", Text),
        Column("commodity", Text),
        Column("variety", Text),
        Column("grade", Text),
        Column("min_price", Numeric(12, 2)),
        Column("max_price", Numeric(12, 2)),
        Column("modal_price", Numeric(12, 2)),
        Column("outlier_flag", Boolean, default=False),
        Column("ingested_at", DateTime(timezone=True), server_default=func.now()),
        schema=s_stg,
    )

    Table(
        "stg_ceda_arrivals",
        metadata,
        Column("stg_id", Integer, primary_key=True, autoincrement=True),
        Column("batch_id", Text, nullable=False),
        Column("source_name", Text, nullable=False),
        Column("raw_file_path", Text),
        Column("arrival_date", Date),
        Column("state", Text),
        Column("district", Text),
        Column("market", Text),
        Column("commodity", Text),
        Column("quantity_tonnes", Numeric(14, 3)),
        Column("ingested_at", DateTime(timezone=True), server_default=func.now()),
        schema=s_stg,
    )

    Table(
        "stg_apy_production",
        metadata,
        Column("stg_id", Integer, primary_key=True, autoincrement=True),
        Column("batch_id", Text, nullable=False),
        Column("source_name", Text, nullable=False),
        Column("raw_file_path", Text),
        Column("crop_year", Integer),
        Column("season", Text),
        Column("state", Text),
        Column("district", Text),
        Column("crop_name", Text),
        Column("crop_type", Text),
        Column("area_ha", Numeric(14, 2)),
        Column("production_t", Numeric(14, 2)),
        Column("yield_t_ha", Numeric(10, 4)),
        Column("ingested_at", DateTime(timezone=True), server_default=func.now()),
        schema=s_stg,
    )

    # Operational
    Table(
        "pipeline_run_log",
        metadata,
        Column("run_id", Integer, primary_key=True, autoincrement=True),
        Column("stage", Text, nullable=False),
        Column("source_name", Text, nullable=False),
        Column("started_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("finished_at", DateTime(timezone=True)),
        Column("rows_fetched", Integer, nullable=False, default=0, server_default="0"),
        Column("rows_loaded", Integer, nullable=False, default=0, server_default="0"),
        Column("rows_rejected", Integer, nullable=False, default=0, server_default="0"),
        Column("status", Text, nullable=False),
        Column("error_message", Text),
        Column("metadata_json", _json_type(), nullable=False, default=dict, server_default="{}"),
        CheckConstraint(
            "status IN ('running', 'success', 'partial_success', 'failed', 'unavailable')",
            name="ck_pipeline_status",
        ),
        schema=s_op,
    )

    Table(
        "dq_rejects",
        metadata,
        Column("reject_id", Integer, primary_key=True, autoincrement=True),
        Column("run_id", Integer, ForeignKey(fk(s_op, "pipeline_run_log.run_id"), ondelete="SET NULL")),
        Column("source_name", Text, nullable=False),
        Column("stage", Text, nullable=False),
        Column("reject_reason", Text, nullable=False),
        Column("raw_record", _json_type(), nullable=False),
        Column("rejected_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        schema=s_op,
    )

    Table(
        "schema_migrations",
        metadata,
        Column("version", Text, primary_key=True),
        Column("checksum", Text, nullable=False),
        Column("description", Text, nullable=False, default="", server_default=""),
        Column("applied_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        schema=s_op,
    )

    # Warehouse
    Table(
        "dim_date",
        metadata,
        Column("date_id", Integer, primary_key=True),
        Column("full_date", Date, unique=True, nullable=False),
        Column("year", SmallInteger, nullable=False),
        Column("month", SmallInteger, nullable=False),
        Column("week", SmallInteger, nullable=False),
        Column("season", Text, nullable=False),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_dim_date_month"),
        CheckConstraint("week BETWEEN 1 AND 53", name="ck_dim_date_week"),
        CheckConstraint("season IN ('Kharif', 'Rabi', 'Zaid')", name="ck_dim_date_season"),
        schema=s_wh,
    )

    Table(
        "dim_commodity",
        metadata,
        Column("commodity_id", Integer, primary_key=True, autoincrement=True),
        Column("commodity_name", Text, unique=True, nullable=False),
        Column("category", Text, nullable=False),
        Column("storable", Boolean, nullable=False, default=True),
        schema=s_wh,
    )

    Table(
        "dim_market",
        metadata,
        Column("market_id", Integer, primary_key=True, autoincrement=True),
        Column("market_name", Text, nullable=False),
        Column("district", Text, nullable=False, default="Unknown"),
        Column("state", Text, nullable=False),
        UniqueConstraint("market_name", "district", "state", name="uq_dim_market_nat"),
        schema=s_wh,
    )

    Table(
        "dim_crop",
        metadata,
        Column("crop_id", Integer, primary_key=True, autoincrement=True),
        Column("crop_name", Text, unique=True, nullable=False),
        Column("crop_type", Text),
        schema=s_wh,
    )

    Table(
        "map_commodity_crop",
        metadata,
        Column("commodity_id", Integer, ForeignKey(fk(s_wh, "dim_commodity.commodity_id"), ondelete="CASCADE"), nullable=False),
        Column("crop_id", Integer, ForeignKey(fk(s_wh, "dim_crop.crop_id"), ondelete="CASCADE"), nullable=False),
        PrimaryKeyConstraint("commodity_id", "crop_id"),
        schema=s_wh,
    )

    Table(
        "fact_price_daily",
        metadata,
        Column("date_id", Integer, ForeignKey(fk(s_wh, "dim_date.date_id")), nullable=False),
        Column("market_id", Integer, ForeignKey(fk(s_wh, "dim_market.market_id")), nullable=False),
        Column("commodity_id", Integer, ForeignKey(fk(s_wh, "dim_commodity.commodity_id")), nullable=False),
        Column("variety", Text, nullable=False, default="NA"),
        Column("grade", Text, nullable=False, default="NA"),
        Column("min_price", Numeric(10, 2), nullable=False),
        Column("max_price", Numeric(10, 2), nullable=False),
        Column("modal_price", Numeric(10, 2), nullable=False),
        Column("outlier_flag", Boolean, nullable=False, default=False),
        Column("source_name", Text, nullable=False, default="agmarknet_api"),
        Column("ingested_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        PrimaryKeyConstraint("date_id", "market_id", "commodity_id", "variety", "grade"),
        CheckConstraint("min_price > 0", name="ck_price_min_pos"),
        CheckConstraint("max_price >= min_price", name="ck_price_max_ge_min"),
        CheckConstraint("modal_price > 0 AND modal_price BETWEEN min_price AND max_price", name="ck_price_modal_range"),
        Index("idx_wh_price_comm_date", "commodity_id", "date_id"),
        Index("idx_wh_price_mkt_comm_date", "market_id", "commodity_id", "date_id"),
        schema=s_wh,
    )

    Table(
        "fact_arrivals_daily",
        metadata,
        Column("date_id", Integer, ForeignKey(fk(s_wh, "dim_date.date_id")), nullable=False),
        Column("market_id", Integer, ForeignKey(fk(s_wh, "dim_market.market_id")), nullable=False),
        Column("commodity_id", Integer, ForeignKey(fk(s_wh, "dim_commodity.commodity_id")), nullable=False),
        Column("quantity_tonnes", Numeric(12, 3), nullable=False),
        Column("source_name", Text, nullable=False, default="ceda_archive"),
        Column("ingested_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        PrimaryKeyConstraint("date_id", "market_id", "commodity_id"),
        CheckConstraint("quantity_tonnes >= 0", name="ck_arrivals_nonneg"),
        schema=s_wh,
    )

    Table(
        "fact_crop_production",
        metadata,
        Column("crop_year", Integer, nullable=False),
        Column("season", Text, nullable=False),
        Column("state", Text, nullable=False),
        Column("district", Text, nullable=False),
        Column("crop_id", Integer, ForeignKey(fk(s_wh, "dim_crop.crop_id")), nullable=False),
        Column("area_ha", Numeric(14, 2)),
        Column("production_t", Numeric(14, 2)),
        Column("yield_t_ha", Numeric(10, 4)),
        Column("source_name", Text, nullable=False, default="des_apy"),
        PrimaryKeyConstraint("crop_year", "season", "state", "district", "crop_id"),
        schema=s_wh,
    )

    # Analytics outputs
    Table(
        "market_signals",
        metadata,
        Column("signal_id", Integer, primary_key=True, autoincrement=True),
        Column("signal_date", Date, nullable=False),
        Column("commodity_id", Integer, ForeignKey(fk(s_wh, "dim_commodity.commodity_id")), nullable=False),
        Column("market_id", Integer, ForeignKey(fk(s_wh, "dim_market.market_id")), nullable=False),
        Column("verdict_class", Text, nullable=False),
        Column("verdict_label", Text, nullable=False),
        Column("current_modal_price", Numeric(10, 2)),
        Column("price_change_4w_pct", Numeric(8, 2)),
        Column("vs_seasonal_avg_pct", Numeric(8, 2)),
        Column("arrivals_trend", Text, nullable=False),
        Column("arrivals_change_14d_pct", Numeric(8, 2)),
        Column("cv_12m", Numeric(8, 4)),
        Column("state_price_gap_pct", Numeric(8, 2)),
        Column("storable", Boolean, nullable=False),
        Column("evidence_completeness", Text, nullable=False),
        Column("observation_count_90d", Integer, nullable=False, default=0),
        Column("analysis_window_start", Date),
        Column("analysis_window_end", Date),
        Column("data_sources", Text),
        Column("rationale_metrics", _json_type(), nullable=False, default=dict),
        Column("threshold_config", _json_type(), nullable=False, default=dict),
        Column("computed_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        UniqueConstraint("signal_date", "commodity_id", "market_id", name="uq_signal_date_comm_mkt"),
        Index("idx_market_signals_lookup", "commodity_id", "market_id", "signal_date"),
        schema=s_an,
    )

    Table(
        "briefings",
        metadata,
        Column("briefing_id", Integer, primary_key=True, autoincrement=True),
        Column("commodity_id", Integer, ForeignKey(fk(s_wh, "dim_commodity.commodity_id")), nullable=False),
        Column("market_id", Integer, ForeignKey(fk(s_wh, "dim_market.market_id")), nullable=False),
        Column("signal_id", Integer, ForeignKey(fk(s_an, "market_signals.signal_id"), ondelete="SET NULL")),
        Column("generated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("signal_json", _json_type(), nullable=False),
        Column("verdict", Text, nullable=False),
        Column("reason_en", Text, nullable=False),
        Column("caution_en", Text, nullable=False),
        Column("reason_hi", Text, nullable=False),
        Column("caution_hi", Text, nullable=False),
        Column("validation_status", Text, nullable=False),
        Column("validation_errors", _json_type(), nullable=False, default=list),
        Column("model_used", Text, nullable=False),
        Column("used_fallback", Boolean, nullable=False, default=False),
        schema=s_an,
    )

    return metadata
