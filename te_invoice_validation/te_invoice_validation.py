"""
TE Invoice Validation flow (DuckDB port of the Oracle RD_T_TE_INVOICE_VALIDATION load).

For every TE subscriber on the run date, it compares the RADIUS, BSCS (billing)
and IN (charging) status, gives a CASE_COMMENT for the combination, and adds the
PCRF status, quota name (from the quota lookup flow) and speeds.

Usage:
    python te_invoice_validation/te_invoice_validation.py [YYYYMMDD]

See te_invoice_validation/README.md for inputs, outputs and dependencies.
"""

import os
import re
import sys
import duckdb


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

from config.duckdb_connection import duckdb_connection


con = duckdb_connection()

# Business date being validated (YYYYMMDD) - the "$1" of the original shell script.
# It can be passed on the command line; otherwise this default is used.
run_date = sys.argv[1] if len(sys.argv) > 1 else "20260928"
if not re.fullmatch(r"\d{8}", run_date):
    raise ValueError(f"run_date must be YYYYMMDD, got: {run_date!r}")
run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"

# Date stamp of the export files on disk (YYYYMMDD).
# Defaults to run_date - change it only if the snapshot file has a different date.
file_date = run_date

# Source exports (one per Oracle table in the original query)
te_path = f"/project/PLATFORM_INTEGRITY/exports/TE_data_{run_date}.csv"         # RD_T_TE_DATA_BASE_HISTORICAL (output of te_data/te_data.py)
bscs_path = f"/RAIDdata/PI/PI_PRJ/exports/bscs_profiles_adsl_{file_date}.csv"    # PFM_BSCS_ADSL
pcrf_path = f"/RAIDdata/PI/PI_PRJ/exports/pcrf_{file_date}.csv"                  # PFM_PCRF_FILTERED
radius_path = f"/RAIDdata/PI/PI_PRJ/exports/radius_{file_date}.csv"              # PFM_RADIUS
charging_path = f"/RAIDdata/PI/PI_PRJ/exports/charging_adsl_{file_date}.csv"     # RD_T_IN_ADSL_HISTORICAL

# QUOTA_NAME_LKP = output of the quota lookup flow (quota_lkb_<run_date>.csv)
quota_path = f"/project/PLATFORM_INTEGRITY/exports/quota_lkb_{run_date}.csv"

# Target: RD_T_TE_INVOICE_VALIDATION
export_path = f"/project/PLATFORM_INTEGRITY/exports/te_invoice_validation_{run_date}.csv"

for p in (te_path, bscs_path, pcrf_path, radius_path, charging_path):
    if not os.path.exists(p):
        raise FileNotFoundError(f"Input file not found: {p}")
if not os.path.exists(quota_path):
    raise FileNotFoundError(
        f"Quota lookup file not found: {quota_path}\n"
        f"Run the quota lookup flow for {run_date} first."
    )


# Read every column as text so that:
#  - MSISDNs keep their leading zeros and join the same way across all sources
#  - status codes and dates are interpreted explicitly below, not guessed
CSV_OPTS = "all_varchar = TRUE, ignore_errors = TRUE, delim = ','"


def txt(col):
    """Trimmed text value; empty string -> NULL (Oracle treats '' as NULL)."""
    return f"NULLIF(TRIM({col}), '')"


def date_matches(col):
    """
    True when the column equals the run date.
    Covers both original filters: run_date = to_number('$1') (stored as 20260928)
    and run_date = to_date('$1','YYYYMMDD') (stored as 2026-09-28, midnight only).
    """
    return (
        f"COALESCE(TRY_STRPTIME(TRIM({col}), '%Y%m%d'), "
        f"TRY_CAST(TRIM({col}) AS TIMESTAMP)) = TIMESTAMP '{run_date_formatted} 00:00:00'"
    )


con.execute(f"""
        CREATE OR REPLACE TEMP TABLE te_invoice_validation AS

        WITH
        -- RD_T_TE_DATA_BASE_HISTORICAL
        -- (outer filter WHERE run_date = to_date('$1') only touches TE columns,
        --  so it is applied here; with LEFT JOINs the result is identical)
        TE_SRC AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('TE_SPEED')}                                    AS TE_SPEED
            FROM read_csv_auto('{te_path}', {CSV_OPTS})
            WHERE {date_matches('RUN_DATE')}
        ),

        -- PFM_BSCS_ADSL WHERE run_date = to_number('$1')
        BSCS AS (
            SELECT DISTINCT
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('STATUS')}                                      AS STATUS,
                {txt('"TYPE"')}                                      AS "TYPE"
            FROM read_csv_auto('{bscs_path}', {CSV_OPTS})
            WHERE {date_matches('RUN_DATE')}
        ),

        -- PFM_PCRF_FILTERED WHERE RUN_DATE = to_date('$1','YYYYMMDD')
        PCRF AS (
            SELECT DISTINCT
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('CUSTOMER_STATUS')}                             AS CUSTOMER_STATUS
            FROM read_csv_auto('{pcrf_path}', {CSV_OPTS})
            WHERE {date_matches('RUN_DATE')}
        ),

        -- PFM_RADIUS WHERE run_date = to_number('$1')
        RADIUS AS (
            SELECT DISTINCT
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('STATUS')}                                      AS STATUS
            FROM read_csv_auto('{radius_path}', {CSV_OPTS})
            WHERE {date_matches('RUN_DATE')}
        ),

        -- RD_T_IN_ADSL_HISTORICAL
        -- WHERE PACKAGE_TYPE = 'DSL Status' AND run_date = to_date('$1','YYYYMMDD')
        CHARGING AS (
            SELECT DISTINCT
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('STATUS')}                                      AS STATUS
            FROM read_csv_auto('{charging_path}', {CSV_OPTS})
            WHERE {txt('PACKAGE_TYPE')} = 'DSL Status'
              AND {date_matches('RUN_DATE')}
        ),

        -- QUOTA_NAME_LKP WHERE Time = to_date('$1','YYYYMMDD')
        QUOTA AS (
            SELECT DISTINCT
                {txt('MSISDN')}                                      AS MSISDN,
                {txt('QUOTA_NAME')}                                  AS QUOTA_NAME
            FROM read_csv_auto('{quota_path}', {CSV_OPTS})
            WHERE {date_matches('"TIME"')}
        ),

        -- Inner query of the original SELECT
        TE AS (
            SELECT DISTINCT
                TE_SRC.RUN_DATE,
                TE_SRC.MSISDN                                        AS TE_MSISDN,

                -- NVL(UPPER(DECODE(RADIUS.STATUS,'0','DeActive','1','Active','2','Suspended',RADIUS.STATUS)),'Not_Exist')
                COALESCE(UPPER(CASE RADIUS.STATUS
                                   WHEN '0' THEN 'DeActive'
                                   WHEN '1' THEN 'Active'
                                   WHEN '2' THEN 'Suspended'
                                   ELSE RADIUS.STATUS
                               END), 'Not_Exist')                    AS RADIUS_STATUS,

                -- NVL(BSCS.STATUS,'Not_Exist')  (no UPPER, as in the original)
                COALESCE(BSCS.STATUS, 'Not_Exist')                   AS BILLING_STATUS,

                -- NVL(UPPER(CHARGING.STATUS),'Not_Exist')
                COALESCE(UPPER(CHARGING.STATUS), 'Not_Exist')        AS CHARGING_STATUS,

                BSCS."TYPE"                                          AS CUSTOMER_TYPE,
                TE_SRC.TE_SPEED,

                -- UPPER(DECODE(PCRF.Customer_STATUS,0,'DeActive',1,'Active',2,'Suspended',3,'On-Hold',PCRF.Customer_STATUS))
                -- (numeric comparison, as in the original; no NVL)
                UPPER(CASE TRY_CAST(PCRF.CUSTOMER_STATUS AS DOUBLE)
                          WHEN 0 THEN 'DeActive'
                          WHEN 1 THEN 'Active'
                          WHEN 2 THEN 'Suspended'
                          WHEN 3 THEN 'On-Hold'
                          ELSE PCRF.CUSTOMER_STATUS
                      END)                                           AS PCRF_STATUS

            FROM TE_SRC
            LEFT OUTER JOIN BSCS     ON TE_SRC.MSISDN = BSCS.MSISDN
            LEFT OUTER JOIN PCRF     ON TE_SRC.MSISDN = PCRF.MSISDN
            LEFT OUTER JOIN RADIUS   ON TE_SRC.MSISDN = RADIUS.MSISDN
            LEFT OUTER JOIN CHARGING ON TE_SRC.MSISDN = CHARGING.MSISDN
        )

        SELECT DISTINCT
            TE.RUN_DATE,
            TE.TE_MSISDN,
            TE.RADIUS_STATUS,
            TE.BILLING_STATUS,
            TE.CHARGING_STATUS,
            (CASE
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='Not_Exist' THEN 'ACTIVE'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='ACTIVE'    THEN 'ACTIVE'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='ACTIVE'    THEN 'ACTIVE'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='SUSPENDED' AND CHARGING_STATUS='SUSPENDED' THEN 'SUSPENDED'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='SUSPENDED' AND CHARGING_STATUS='ACTIVE'    THEN 'SUSPENDED'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='SUSPENDED' THEN 'SUSPENDED'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='SUSPENDED' AND CHARGING_STATUS='Not_Exist' THEN 'SUSPENDED'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='SUSPENDED' THEN 'SUSPENDED'
                WHEN RADIUS_STATUS='Not_Exist' AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='Not_Exist' THEN 'Not_Exist'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='Not_Exist' THEN 'Not_Exist/DEACTIVE'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='SUSPENDED' AND CHARGING_STATUS='Not_Exist' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='ACTIVE'    AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='Not_Exist' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='Not_Exist' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='Not_Exist' AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='ACTIVE'    THEN 'MISMATCH'
                WHEN RADIUS_STATUS='Not_Exist' AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='Not_Exist' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='SUSPENDED' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='ACTIVE'    AND CHARGING_STATUS='ACTIVE'    THEN 'MISMATCH'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='ACTIVE'    THEN 'MISMATCH'
                WHEN RADIUS_STATUS='Not_Exist' AND BILLING_STATUS='SUSPENDED' AND CHARGING_STATUS='Not_Exist' THEN 'MISMATCH'
                WHEN RADIUS_STATUS='DEACTIVE'  AND BILLING_STATUS='Not_Exist' AND CHARGING_STATUS='SUSPENDED' THEN 'MISMATCH'
                ELSE '_N'
            END)                                                     AS CASE_COMMENT,
            TE.CUSTOMER_TYPE,
            TE.PCRF_STATUS,
            QUOTA.QUOTA_NAME,
            TE.TE_SPEED                                              AS TE_SPEED,
            -- REGEXP_SUBSTR(QUOTA.QUOTA_NAME,'[0-9]+M',1,1)  (no match -> NULL, as in Oracle)
            NULLIF(regexp_extract(QUOTA.QUOTA_NAME, '[0-9]+M'), '') AS SPEED
        FROM TE
        LEFT OUTER JOIN QUOTA
            ON TE.TE_MSISDN = QUOTA.MSISDN
""")

# Second step of the original script:
# UPDATE RD_T_TE_INVOICE_VALIDATION SET CUSTOMER_TYPE = 'NA' WHERE CUSTOMER_TYPE IS NULL;
con.execute("""
    UPDATE te_invoice_validation
    SET CUSTOMER_TYPE = 'NA'
    WHERE CUSTOMER_TYPE IS NULL
""")

# Result is computed once into a temp table, then reused for display, count and export
result = con.sql("SELECT * FROM te_invoice_validation ORDER BY TE_MSISDN")
print(result)

row_count = con.execute("SELECT COUNT(*) FROM te_invoice_validation").fetchall()[0][0]
print(f"Row count: {row_count}")

print(con.sql("""
    SELECT CASE_COMMENT, COUNT(*) AS SUBSCRIBERS
    FROM te_invoice_validation
    GROUP BY CASE_COMMENT
    ORDER BY SUBSCRIBERS DESC
"""))

os.makedirs(os.path.dirname(export_path), exist_ok=True)
con.execute(f"""
    COPY (SELECT * FROM te_invoice_validation ORDER BY TE_MSISDN)
    TO '{export_path}' (HEADER, DELIMITER ',')
""")
print(f"Exported to: {export_path}")
