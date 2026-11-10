import os
import sys

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

import re
import argparse
from datetime import datetime

import duckdb
from config.duckdb_connection import duckdb_connection
from utils.logger import get_logger
from utils.send_mail import send_mail
from utils.export_to_oracle import prepare_ctl
from utils.export_to_oracle import run_sqlldr
from utils.dump_vs_database import validate_loaded_data


# Raw TE file dropped by the business: one CSV per run date
TE_INPUT_PATTERN = "/RAIDdata/PI/TE_DATA/raw/TE_NUMBERS_{run_date}.csv"

# Output read by the downstream flows (e.g. te_invoice_validation as te_path)
TE_CSV_PATTERN = "/project/PLATFORM_INTEGRITY/exports/TE_data_{run_date}.csv"


def _sql_str(value: str) -> str:
    """Quote a value as a SQL string literal (handles ' in file names)."""
    return "'" + value.replace("'", "''") + "'"


def _te_select(file_path: str, run_date_formatted: str) -> str:
    """
    SELECT for the TE input CSV (every column read as text).

    MSISDN normalisation (result is always 20 + national number):
      - plain numbers (225551234, 0225551234, 225551234.0) are cast to an
        integer, which drops any leading 0 and trailing .0
      - values with spaces/dashes/+ are reduced to their digits
      - numbers that already carry the 20 country code (10-12 digits starting
        with 20, e.g. 2035484718 / 20225551234 / 201001234567) are not prefixed
        a second time; national numbers never start with 20 at those lengths
    """
    tel_txt = "TRIM(CAST(TEL AS VARCHAR))"
    digits = f"""
        COALESCE(
            CAST(TRY_CAST(TRY_CAST({tel_txt} AS DECIMAL(20,0)) AS BIGINT) AS VARCHAR),
            NULLIF(LTRIM(regexp_replace({tel_txt}, '[^0-9]', '', 'g'), '0'), '')
        )"""

    return f"""
        SELECT
            CASE
                WHEN digits LIKE '20%' AND LENGTH(digits) BETWEEN 10 AND 12 THEN digits
                ELSE '20' || digits
            END                                                  AS MSISDN,
            TE_SPEED,
            RUN_DATE
        FROM (
            SELECT
                {digits}                                         AS digits,
                -- speeds exported as 30.0 -> keep them as 30
                NULLIF(regexp_replace(TRIM(CAST(Speed AS VARCHAR)), '\\.0+$', ''), '')
                                                                 AS TE_SPEED,
                DATE '{run_date_formatted}'                      AS RUN_DATE
            FROM read_csv({_sql_str(file_path)}, header = TRUE, all_varchar = TRUE)
        )
    """


def load_TE_data(run_date: str, con=None):

    logger = get_logger(
        flow="TE_data",
        log_dir=None,
        run_date=run_date
    )

    try:
        if not re.fullmatch(r"\d{8}", run_date or ""):
            raise ValueError(f"run_date must be YYYYMMDD, got: {run_date!r}")
        datetime.strptime(run_date, "%Y%m%d")  # rejects impossible dates like 20261340

        if con is None:
            con = duckdb_connection()

        start_time = datetime.now()
        logger.info(f"###################################################")
        logger.info(f"###### TE_data started for run_date= {run_date} ######")
        logger.info(f"###################################################")
        run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"
        csv_path = TE_CSV_PATTERN.format(run_date=run_date)

        file_path = TE_INPUT_PATTERN.format(run_date=run_date)

        if not os.path.isfile(file_path):
            raise FileNotFoundError(
                f"TE input file not found for run_date= {run_date}: {file_path}"
            )
        logger.info(f"Reading TE_data file: {file_path}")

        query = _te_select(file_path, run_date_formatted)

        con.execute(f""" CREATE OR REPLACE TEMP TABLE TE_data_raw AS {query} """)

        # Rows without a usable phone number (empty TEL, text in TEL, ...)
        # cannot be matched by any downstream flow, so they are not loaded.
        skipped = con.execute(
            "SELECT COUNT(*) FROM TE_data_raw WHERE MSISDN IS NULL"
        ).fetchone()[0]
        if skipped:
            logger.warning(f"Skipped {skipped} rows with an empty or invalid TEL")

        con.execute("""
            CREATE OR REPLACE TABLE TE_data AS
            SELECT RUN_DATE, MSISDN, TE_SPEED
            FROM TE_data_raw
            WHERE MSISDN IS NOT NULL
        """)

        count = con.execute("SELECT COUNT(*) FROM TE_data").fetchone()[0]
        logger.info(f"Total processed rows: {count}")

        if count == 0:
            raise ValueError(
                f"No TE_data rows processed for run_date= {run_date} "
                f"(file: {file_path}, {skipped} row(s) skipped)"
            )

        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        logger.info(f"Exporting {os.path.basename(csv_path)} ..")
        con.execute(f""" COPY (SELECT RUN_DATE, MSISDN, TE_SPEED FROM TE_data) TO {_sql_str(csv_path)} (DELIMITER ',', HEADER TRUE, QUOTE '"', ESCAPE '"'); """)

        logger.info(f"Inserting into PI.TE_data table ..")
        ctl_file = prepare_ctl("TE_data", run_date)
        run_sqlldr("TE_data", run_date, ctl_file=ctl_file)
        logger.info(f"Insertion done")

        logger.info(f"Validating inserted data ..")
        validate_loaded_data(
            flow_name="TE_data",
            run_date=run_date,
            expected_rows=count,
            logger=logger
        )
        logger.info("Inserted data validated successfully.")

        end_time = datetime.now()
        logger.info(f"TE_data finished")
        logger.info(f"TE_data total duration: {end_time - start_time}")
        send_mail(
            subject="TE DATA Flow Finished",
            content=f"""
                Run Date : {run_date}
                File      : {os.path.basename(file_path)}
                Rows      : {count}
                Skipped   : {skipped}
                DB Validation : Passed
                Duration  : {end_time - start_time}""")
        return con.table("TE_data")

    except Exception as e:
        logger.exception(f"TE_data failed for run_date= {run_date} | error= {str(e)}")
        try:
            send_mail(
                subject="TE DATA Flow Failed",
                content=f"""
                Run Date : {run_date}
                Error:
                {str(e)}
                """)
        except Exception:
            # a mail failure must not hide the original error
            logger.exception("Could not send the failure e-mail")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run_date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    load_TE_data(run_date=args.run_date)
