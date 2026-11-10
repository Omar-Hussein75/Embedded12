import os
import sys
import duckdb


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

from config.duckdb_connection import duckdb_connection


con = duckdb_connection()

run_date = "20260928"
run_date_formatted = (f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}")
bscs_path = f"/RAIDdata/PI/PI_PRJ/exports/bscs_profiles_adsl_20261031.csv"
charging_path = f"/RAIDdata/PI/PI_PRJ/exports/charging_adsl_20261031.csv"
pcrf_path = f"/RAIDdata/PI/PI_PRJ/exports/pcrf_20261031.csv"
export_path = f"/project/PLATFORM_INTEGRITY/exports/quota_lkb_{run_date}.csv"


# PCRF service names excluded from the Main Bundle lookup
PCRF_EXCLUDED_SERVICES = [
    "Serv_DSL_Generic_Promo",
    "Serv_DSL_Compensation_Gift",
    "Serv_DSL_Throttling",
    "ADSL_OnHold",
    "Serv_DSL_Double_Quota_Pre",
]

result = con.query(f"""

        WITH
        BSCS AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(PCRF_SERVICE_NAME_EXPECTED AS VARCHAR)          AS SERVICE_NAME,
                'BSCS'                                               AS SOURCE
            FROM read_csv_auto('{bscs_path}',ignore_errors = TRUE, delim = ',')
            WHERE TRY_CAST(RUN_DATE AS BIGINT) = {run_date}
              AND "TYPE" = 'POST'
              AND NULLIF(TM_CODE_DESC, '') NOT LIKE 'SDSL%'
              AND NULLIF(TM_CODE_DESC, '') NOT LIKE 'Cloud%'
        ),

        IN_SRC AS (
            SELECT
                TRY_CAST(RUN_DATE AS DATE)                           AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(PCRF_SERVICE_NAME AS VARCHAR)            AS SERVICE_NAME,
                'IN'                                                 AS SOURCE
            FROM read_csv_auto('{charging_path}',ignore_errors = TRUE, delim = ',')
            WHERE TRY_CAST(RUN_DATE AS DATE) = DATE '{run_date_formatted}'
              AND PROD_TYPE_NAME = 'Main Bundle'
        ),

    
        PCRF AS (
            SELECT
                DATE '{run_date_formatted}'                          AS RUN_DATE,
                CAST(MSISDN AS VARCHAR)                              AS MSISDN,
                CAST(SERVICE_NAME AS VARCHAR)                        AS SERVICE_NAME,
                'PCRF'                                               AS SOURCE
            FROM read_csv_auto('{pcrf_path}',ignore_errors = TRUE, delim = ',')
            WHERE TRY_CAST(RUN_DATE AS BIGINT) <= {run_date}
              AND PROD_TYPE_NAME = 'Main Bundle'
              AND COALESCE(SERVICE_NAME, '') NOT IN (
                        'Serv_DSL_Generic_Promo',
                        'Serv_DSL_Compensation_Gift',
                        'Serv_DSL_Throttling',
                        'ADSL_OnHold',
                        'Serv_DSL_Double_Quota_Pre'
                )
              AND SERVICE_NAME NOT LIKE '%Add%'
              AND SERVICE_NAME NOT LIKE '%DoubleQuota%'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY MSISDN
                ORDER BY TRY_CAST(RUN_DATE AS BIGINT) DESC
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
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY MSISDN
            ORDER BY RUN_DATE DESC
        ) = 1
""")

print(result)

row_count = result.aggregate("count(*) AS row_count").fetchone()[0]
print(f"Row count: {row_count}")
