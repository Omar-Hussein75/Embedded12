import os
import sys

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

import duckdb
import argparse
from datetime import datetime
from config.duckdb_connection import duckdb_connection
from utils.logger import get_logger
from utils.send_mail import send_mail
from utils.export_to_oracle import prepare_ctl
from utils.export_to_oracle import run_sqlldr
from utils.dump_vs_database import validate_loaded_data


FLOW_NAME = "QUOTA_LKB"

# Source tables expected in the DuckDB connection (loaded by upstream flows).
# Change the names here if they differ in your DuckDB database.
SOURCE_TABLES = {
    "BSCS": "PFM_BSCS_ADSL",
    "IN":   "RD_T_IN_ADSL_HISTORICAL",
    "PCRF": "PFM_PCRF",
}

# PCRF service names excluded from the Main Bundle lookup
PCRF_EXCLUDED_SERVICES = [
    "Serv_DSL_Generic_Promo",
    "Serv_DSL_Compensation_Gift",
    "Serv_DSL_Throttling",
    "ADSL_OnHold",
    "Serv_DSL_Double_Quota_Pre",
]


def load_QUOTA_LKB(run_date: str, con=None):

    logger = get_logger(
        flow=FLOW_NAME,
        log_dir=None,
        run_date=run_date
    )

    try:
        if con is None:
            con = duckdb_connection()

        start_time = datetime.now()
        run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"

        export_path = f"/project/PLATFORM_INTEGRITY/exports/{FLOW_NAME}_{run_date}.csv"

        # Fail early with a clear message if an upstream table is missing
        existing = {
            r[0].upper()
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        missing = [t for t in SOURCE_TABLES.values() if t.upper() not in existing]
        if missing:
            raise ValueError(f"Missing source tables in DuckDB: {missing}")

        t = SOURCE_TABLES
        excluded = ", ".join(f"'{s}'" for s in PCRF_EXCLUDED_SERVICES)

        con.execute(f"""
        CREATE OR REPLACE TABLE {FLOW_NAME} AS

        WITH
        -- Branch 1: BSCS postpaid, RUN_DATE stored as number YYYYMMDD
        -- NULLIF(...,'') mimics Oracle, where '' is NULL and fails NOT LIKE
        BSCS AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(PCRF_SERVICE_NAME_EXPECTED AS VARCHAR)          AS SERVICE_NAME,
                'BSCS'                                               AS SOURCE
            FROM {t['BSCS']}
            WHERE TRY_CAST(RUN_DATE AS BIGINT) = {run_date}
              AND "TYPE" = 'POST'
              AND NULLIF(TMCODE_DES, '') NOT LIKE 'SDSL%'
              AND NULLIF(TMCODE_DES, '') NOT LIKE 'Cloud%'
        ),

        -- Branch 2: IN Main Bundle, RUN_DATE stored as DATE
        IN_SRC AS (
            SELECT
                TRY_CAST(RUN_DATE AS DATE)                           AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(PCRF_SERVICE_ID_EXPECTED AS VARCHAR)            AS SERVICE_NAME,
                'IN'                                                 AS SOURCE
            FROM {t['IN']}
            WHERE TRY_CAST(RUN_DATE AS DATE) = DATE '{run_date_formatted}'
              AND PACKAGE_TYPE = 'Main Bundle'
        ),

        -- Branch 3: latest PCRF Main Bundle per MSISDN up to run_date
        -- (RUN_DATE stored as number YYYYMMDD)
        PCRF AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(SERVICE_NAME AS VARCHAR)                        AS SERVICE_NAME,
                'PCRF'                                               AS SOURCE
            FROM {t['PCRF']}
            WHERE TRY_CAST(RUN_DATE AS BIGINT) <= {run_date}
              AND PACKAGE_TYPE = 'Main Bundle'
              AND NULLIF(SERVICE_NAME, '') NOT IN ({excluded})
              AND SERVICE_NAME NOT LIKE '%Add%'
              AND SERVICE_NAME NOT LIKE '%DoubleQuota%'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY MSISDN
                ORDER BY TRY_CAST(RUN_DATE AS BIGINT) DESC
            ) = 1
        ),

        -- Oracle UNION = distinct across all three branches
        ALL_SOURCES AS (
            SELECT * FROM BSCS
            UNION
            SELECT * FROM IN_SRC
            UNION
            SELECT * FROM PCRF
        )

        -- One quota per MSISDN.
        -- NOTE: all branches carry the same RUN_DATE, so ORDER BY RUN_DATE DESC is a tie
        -- and Oracle picks an arbitrary source; DuckDB behaves the same way.
        SELECT
            DATE '{run_date_formatted}'          AS TIME,
            MSISDN,
            SERVICE_NAME                         AS QUOTA_NAME,
            SOURCE
        FROM ALL_SOURCES
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY MSISDN
            ORDER BY RUN_DATE DESC
        ) = 1
        """)

        count = con.execute(
            f"SELECT COUNT(*) FROM {FLOW_NAME}"
        ).fetchone()[0]

        logger.info(f"Total processed rows: {count}")

        if count == 0:
            raise ValueError(f"No {FLOW_NAME} data processed for run_date={run_date}")

        breakdown = con.execute(f"""
            SELECT SOURCE, COUNT(*)
            FROM {FLOW_NAME}
            GROUP BY SOURCE
            ORDER BY 2 DESC
        """).fetchall()
        logger.info(f"SOURCE breakdown: {breakdown}")

        con.execute(f"""
        COPY (
            SELECT *
            FROM {FLOW_NAME}
        )
        TO '{export_path}'
        (DELIMITER ',', HEADER TRUE, QUOTE '"', ESCAPE '"');
        """)

        logger.info(f"Inserting into {FLOW_NAME} table ..")

        ctl_file = prepare_ctl(FLOW_NAME, run_date)

        run_sqlldr(
            FLOW_NAME,
            run_date,
            ctl_file=ctl_file
        )

        logger.info("Insertion done")

        logger.info("Validating inserted data ..")

        validate_loaded_data(
            flow_name=FLOW_NAME,
            run_date=run_date,
            expected_rows=count,
            logger=logger
        )

        logger.info("Inserted data validated successfully.")

        end_time = datetime.now()

        logger.info(f"{FLOW_NAME} job finished")
        logger.info(
            f"{FLOW_NAME} duration: {end_time - start_time}"
        )

        send_mail(
            subject=f"{FLOW_NAME} Flow Finished",
            content=f"""
Run Date        : {run_date}
Rows            : {count}
DB Validation   : Passed
Duration        : {end_time - start_time}
"""
        )

        return con.table(FLOW_NAME)

    except Exception as e:
        logger.exception(
            f"{FLOW_NAME} failed for run_date={run_date} | error={str(e)}"
        )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run_date", required=True)
    args = parser.parse_args()

    load_QUOTA_LKB(run_date=args.run_date)
