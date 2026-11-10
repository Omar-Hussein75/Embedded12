import os
import sys

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

import re
import glob
import argparse
from datetime import datetime

import duckdb
from config.duckdb_connection import duckdb_connection
from utils.logger import get_logger
from utils.send_mail import send_mail
from utils.export_to_oracle import prepare_ctl
from utils.export_to_oracle import run_sqlldr
from utils.dump_vs_database import validate_loaded_data


# Raw TE files dropped by the business.
# Only files whose name contains the run date are loaded, e.g.
#   TE_Numbers_20260928.xlsx / TE_Numbers 2026-09-28.xlsx / TE_Numbers_2026_09_28.xlsx
# Revisit once the file naming is aligned with the business.
TE_FILES_PATTERN = "/RAIDdata/PI/TE_DATA/raw/TE_Numbers*.xlsx"

# Output read by the downstream flows (e.g. te_invoice_validation as te_path)
TE_CSV_PATTERN = "/project/PLATFORM_INTEGRITY/exports/TE_data_{run_date}.csv"


def _te_files_for_run_date(run_date: str) -> list:
    """TE files whose name contains the run date (YYYYMMDD, YYYY-MM-DD or YYYY_MM_DD)."""
    y, m, d = run_date[:4], run_date[4:6], run_date[6:]
    # the date must not be part of a longer number (e.g. 1202609281)
    date_re = re.compile(rf"(?<!\d)(?:{y}{m}{d}|{y}-{m}-{d}|{y}_{m}_{d})(?!\d)")
    return sorted(
        f for f in glob.glob(TE_FILES_PATTERN)
        if date_re.search(os.path.basename(f))
    )


def _sql_str(value: str) -> str:
    """Quote a value as a SQL string literal (handles ' in file names)."""
    return "'" + value.replace("'", "''") + "'"


def _te_select(file_path: str, run_date_formatted: str) -> str:
    """
    SELECT for one TE workbook.

    MSISDN normalisation (result is always 20 + national number):
      - numeric cells (read as 225551234 or 225551234.0) are cast to an integer,
        which drops the trailing .0 and any leading 0
      - text cells with spaces/dashes/+ are reduced to their digits
      - numbers that already carry the 20 country code (11 digits starting
        with 20) are not prefixed a second time
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
                WHEN digits LIKE '20%' AND LENGTH(digits) = 11 THEN digits
                ELSE '20' || digits
            END                                                  AS MSISDN,
            TE_SPEED,
            RUN_DATE
        FROM (
            SELECT
                {digits}                                         AS digits,
                -- numeric speed cells come back as 30.0 -> keep them as 30
                NULLIF(regexp_replace(TRIM(CAST(Speed AS VARCHAR)), '\\.0+$', ''), '')
                                                                 AS TE_SPEED,
                DATE '{run_date_formatted}'                      AS RUN_DATE
            FROM read_xlsx({_sql_str(file_path)}, header = True)
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

        files = _te_files_for_run_date(run_date)

        logger.info(f"Found {len(files)} TE_data files:")
        for file_path in files:
            logger.info(f"  {file_path}")

        if not files:
            raise FileNotFoundError(
                f"No TE_data XLSX file for run_date= {run_date} matching "
                f"{TE_FILES_PATTERN} (the file name must contain {run_date} "
                f"or {run_date_formatted})"
            )

        query = "\nUNION ALL\n".join(
            _te_select(file_path, run_date_formatted) for file_path in files
        )

        con.execute(f""" CREATE OR REPLACE TEMP TABLE TE_data_raw AS {query} """)

        # Rows without a usable phone number (blank Excel rows, text in TEL, ...)
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
                f"({len(files)} file(s) read, {skipped} row(s) skipped)"
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
                Files     : {len(files)}
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
