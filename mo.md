import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from config.duckdb_connection import duckdb_connection

RUN_DATE = "20261004"
csv_path = f"/RAIDdata/PI/SDSL/SDSL_Network/in/SDSL_{RUN_DATE}.csv"
OUT_PATH = f"/project/SDSL_OUT/SDSL_NETWORK_RESULT{RUN_DATE}.csv"

con = duckdb_connection()

result = con.query(f"""
    WITH base AS (
    SELECT
        TRY_CAST("SID" AS VARCHAR) AS SID,
        TRY_CAST("PE Interface" AS VARCHAR) AS PE_Interface,
        TRY_CAST("WAN IP" AS VARCHAR) AS WAN_IP,
        TRY_CAST("MGT VRF" AS VARCHAR) AS MGT_VRF,
        TRY_CAST("VRF" AS VARCHAR) AS VRF,
        TRY_CAST("Actual Speed" AS VARCHAR) AS Actual_Speed,
        TRY_CAST("PE Describtion" AS VARCHAR) AS PE_Describtion,
        TRY_CAST("Comments" AS VARCHAR) AS Comments,
        TRY_CAST("PE Speed" AS VARCHAR) AS Original_PE_Speed,
        TRY_CAST("Service Type" AS VARCHAR) AS Service_Type,
        TRY_CAST("Branch Name" AS VARCHAR) AS Branch_Name,
        TRY_CAST("Entry Date" AS VARCHAR) AS Entry_Date,

        TRY_CAST(
            regexp_extract("PE Speed", '([0-9.]+)', 1)
            AS DOUBLE
        )
        *
        CASE regexp_extract(upper("PE Speed"), '([KMG])', 1)
            WHEN 'K' THEN 0.001
            WHEN 'G' THEN 1000
            ELSE 1
        END AS pe_speed_mbps

    FROM read_csv_auto('{csv_path}', ignore_errors=True)
),

sid_rules AS (
    SELECT
        SID,

        COUNT(*) FILTER (WHERE MGT_VRF = 'l2') AS l2_count,
        COUNT(*) FILTER (WHERE MGT_VRF = 'l3') AS l3_count,
        SUM(pe_speed_mbps) FILTER (WHERE MGT_VRF = 'l3') AS l3_total_speed,
        MIN(pe_speed_mbps) FILTER (WHERE MGT_VRF = 'l3') AS l3_speed,
        MIN(pe_speed_mbps) FILTER (WHERE MGT_VRF = 'l2') AS l2_speed,

        CASE
            WHEN COUNT(DISTINCT pe_speed_mbps)
                 FILTER (WHERE MGT_VRF = 'l2') > 1
            THEN 'REJECTED: Multiple PE Speeds found in l2'

            WHEN COUNT(*) FILTER (WHERE MGT_VRF = 'l2') > 0
                 AND COUNT(*) FILTER (WHERE MGT_VRF = 'l3') > 0
                 AND COUNT(DISTINCT pe_speed_mbps)
                     FILTER (WHERE MGT_VRF = 'l3') > 1
            THEN 'REJECTED: Mixed l2/l3 SID with multiple l3 speeds'

            WHEN COUNT(*) FILTER (WHERE MGT_VRF = 'l2') > 0
                 AND COUNT(*) FILTER (WHERE MGT_VRF = 'l3') > 0
            THEN 'VALID: Mixed l2/l3 SID using l3 speed'

            WHEN COUNT(*) FILTER (WHERE MGT_VRF = 'l2') > 0
                 AND COUNT(*) FILTER (WHERE MGT_VRF = 'l3') = 0
            THEN 'VALID: l2 only SID'

            WHEN COUNT(*) FILTER (WHERE MGT_VRF = 'l3') > 0
                 AND COUNT(*) FILTER (WHERE MGT_VRF = 'l2') = 0
            THEN 'VALID: l3 only SID'

            ELSE 'VALID: No specific rule applied'
        END AS PE_Speed_Rule_Status

    FROM base
    GROUP BY SID
),

resolved AS (
    SELECT
        b.*,
        r.PE_Speed_Rule_Status,

        CASE
           WHEN r.PE_Speed_Rule_Status LIKE 'REJECTED:%'
           THEN NULL

            WHEN r.l3_count > 0
                 AND r.l2_count = 0
            THEN r.l3_total_speed

            WHEN r.l2_count > 0
                 AND r.l3_count > 0
            THEN r.l3_speed

            WHEN r.l2_count > 0
                 AND r.l3_count = 0
            THEN r.l2_speed

            ELSE b.pe_speed_mbps
        END AS resolved_speed_mbps

    FROM base b
    LEFT JOIN sid_rules r
        ON b.SID = r.SID
)

SELECT
    SID,
    PE_Interface,
    WAN_IP,
    MGT_VRF,
    VRF,
    Actual_Speed,
    PE_Describtion,
    Comments,

    CASE
        WHEN resolved_speed_mbps IS NULL
        THEN Original_PE_Speed

        WHEN resolved_speed_mbps >= 1000
             AND resolved_speed_mbps % 1000 = 0
        THEN
            CAST(
                CAST(resolved_speed_mbps / 1000 AS BIGINT)
                AS VARCHAR
            ) || 'G'

        WHEN resolved_speed_mbps =
             CAST(resolved_speed_mbps AS BIGINT)
        THEN
            CAST(
                CAST(resolved_speed_mbps AS BIGINT)
                AS VARCHAR
            ) || 'M'

        ELSE
            CAST(resolved_speed_mbps AS VARCHAR) || 'M'
    END AS PE_Speed,
    PE_Speed_Rule_Status,
    Service_Type,
    Branch_Name,
    Entry_Date

FROM resolved
""")

result.write_csv(OUT_PATH)

print("CSV exported successfully.")
