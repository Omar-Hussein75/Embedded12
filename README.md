import os
import sys
import duckdb


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

from config.duckdb_connection import duckdb_connection


con = duckdb_connection()

# Business date being checked (YYYYMMDD)
run_date = "20260928"
run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"

# Date stamp of the export files on disk (YYYYMMDD).
# Defaults to run_date - change it only if the snapshot file has a different date.
file_date = run_date

bscs_path = f"/RAIDdata/PI/PI_PRJ/exports/bscs_profiles_adsl_{file_date}.csv"
charging_path = f"/RAIDdata/PI/PI_PRJ/exports/charging_adsl_{file_date}.csv"
pcrf_path = f"/RAIDdata/PI/PI_PRJ/exports/pcrf_{file_date}.csv"
export_path = f"/project/PLATFORM_INTEGRITY/exports/quota_lkb_{run_date}.csv"

for p in (bscs_path, charging_path, pcrf_path):
    if not os.path.exists(p):
        raise FileNotFoundError(f"Input file not found: {p}")


# PCRF service names excluded from the Main Bundle lookup
PCRF_EXCLUDED_SERVICES = [
    "Serv_DSL_Generic_Promo",
    "Serv_DSL_Compensation_Gift",
    "Serv_DSL_Throttling",
    "ADSL_OnHold",
    "Serv_DSL_Double_Quota_Pre",
]
pcrf_excluded_sql = ", ".join(f"'{s}'" for s in PCRF_EXCLUDED_SERVICES)

# Read every column as text so that:
#  - MSISDNs keep their leading zeros and match across all three sources
#  - RUN_DATE is parsed the same way whatever format the file uses
CSV_OPTS = "all_varchar = TRUE, ignore_errors = TRUE, delim = ','"

# Parses RUN_DATE whether it is stored as 20260928 or 2026-09-28
def parse_date(col):
    return (
        f"COALESCE(TRY_STRPTIME(TRIM({col}), '%Y%m%d')::DATE, "
        f"TRY_CAST(TRIM({col}) AS DATE))"
    )


con.execute(f"""
        CREATE OR REPLACE TEMP TABLE quota_lkb AS

        WITH
        BSCS AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                TRIM(MSISDN)                                         AS MSISDN,
                NULLIF(TRIM(PCRF_SERVICE_NAME_EXPECTED), '')         AS SERVICE_NAME,
                'BSCS'                                               AS SOURCE,
                1                                                    AS SOURCE_PRIORITY
            FROM read_csv_auto('{bscs_path}', {CSV_OPTS})
            WHERE {parse_date('RUN_DATE')} = DATE '{run_date_formatted}'
              AND TRIM("TYPE") = 'POST'
              AND COALESCE(TM_CODE_DESC, '') NOT LIKE 'SDSL%'
              AND COALESCE(TM_CODE_DESC, '') NOT LIKE 'Cloud%'
        ),

        IN_SRC AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                TRIM(MSISDN)                                         AS MSISDN,
                NULLIF(TRIM(PCRF_SERVICE_NAME), '')                  AS SERVICE_NAME,
                'IN'                                                 AS SOURCE,
                2                                                    AS SOURCE_PRIORITY
            FROM read_csv_auto('{charging_path}', {CSV_OPTS})
            WHERE {parse_date('RUN_DATE')} = DATE '{run_date_formatted}'
              AND TRIM(PROD_TYPE_NAME) = 'Main Bundle'
        ),

        PCRF_RAW AS (
            SELECT
                {parse_date('RUN_DATE')}                             AS SRC_DATE,
                TRIM(MSISDN)                                         AS MSISDN,
                NULLIF(TRIM(SERVICE_NAME), '')                       AS SERVICE_NAME
            FROM read_csv_auto('{pcrf_path}', {CSV_OPTS})
            WHERE TRIM(PROD_TYPE_NAME) = 'Main Bundle'
        ),

        PCRF AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                MSISDN,
                SERVICE_NAME,
                'PCRF'                                               AS SOURCE,
                3                                                    AS SOURCE_PRIORITY
            FROM PCRF_RAW
            WHERE SRC_DATE <= DATE '{run_date_formatted}'
              AND SERVICE_NAME NOT IN ({pcrf_excluded_sql})
              AND SERVICE_NAME NOT LIKE '%Add%'
              AND SERVICE_NAME NOT LIKE '%DoubleQuota%'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY MSISDN
                ORDER BY SRC_DATE DESC, SERVICE_NAME
            ) = 1
        ),

        ALL_SOURCES AS (
            SELECT * FROM BSCS
            UNION
            SELECT * FROM IN_SRC
            UNION
            SELECT * FROM PCRF
        )

        SELECT
            DATE '{run_date_formatted}'          AS TIME,
            MSISDN,
            SERVICE_NAME                         AS QUOTA_NAME,
            SOURCE
        FROM ALL_SOURCES
        WHERE MSISDN IS NOT NULL AND MSISDN <> ''
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY MSISDN
            ORDER BY (SERVICE_NAME IS NULL), SOURCE_PRIORITY, SERVICE_NAME
        ) = 1
        ORDER BY MSISDN
""")

# Result is computed once into a temp table, then reused for display, count and export
result = con.sql("SELECT * FROM quota_lkb")
print(result)

row_count = con.execute("SELECT COUNT(*) FROM quota_lkb").fetchall()[0][0]
print(f"Row count: {row_count}")

os.makedirs(os.path.dirname(export_path), exist_ok=True)
con.execute(f"COPY quota_lkb TO '{export_path}' (HEADER, DELIMITER ',')")
print(f"Exported to: {export_path}")
