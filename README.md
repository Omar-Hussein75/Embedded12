import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from config.duckdb_connection import duckdb_connection

RUN_DATE = "20261004"
MAI_KPI_PATH = f"/project/PLATFORM_INTEGRITY/exports/sdsl_speed_kpi.csv"
NETWORK_PATH = f"/project/SDSL_OUT/SDSL_NETWORK_RESULT{RUN_DATE}.csv"
run_date_formatted = f"{RUN_DATE[:4]}-{RUN_DATE[4:6]}-{RUN_DATE[6:]}"
billing_path = f"/RAIDdata/PI/SDSL/BSCS_SDSL/raw/SDSL_BSCS_PROFILES_{RUN_DATE}.csv.gz"
sdsl_network_path = f"/RAIDdata/PI/SDSL/SDSL_Network/in/SDSL_{RUN_DATE}.csv"
bscs_sdsl_vs_network_path = f"/project/PLATFORM_INTEGRITY/exports/BSCS_SDSL_VS_NETWORK_202611051550.csv"

con = duckdb_connection()

con.execute(f"""
    CREATE OR REPLACE TABLE bscs_sdsl_vs_network AS
    SELECT
        TRY_CAST(sid AS VARCHAR)             AS sid,
        TRY_CAST(network_status AS VARCHAR)  AS network_status,
        TRY_CAST(billing_status AS VARCHAR)  AS billing_status,
        TRY_CAST(run_date AS DATE)           AS run_date
    FROM read_csv_auto('{bscs_sdsl_vs_network_path}', ignore_errors=True)
""")

con.execute(f"""
    CREATE OR REPLACE TABLE sdsl_network AS
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

        FROM read_csv_auto('{sdsl_network_path}', ignore_errors=True)
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

con.execute(f"""
    CREATE OR REPLACE TABLE bscs_sdsl AS
        SELECT
        TRY_CAST(SID AS VARCHAR) AS SID,
        TRY_CAST(BILLCYCLE AS VARCHAR) AS BILLCYCLE,
        TRY_CAST(RATEPLAN_FLAG AS VARCHAR) AS RATEPLAN_FLAG,
        TRY_CAST(CH_STATUS AS VARCHAR) AS CH_STATUS,
        TRY_CAST(CCNAME AS VARCHAR) AS CCNAME,
        TRY_CAST(CUSTCODE AS VARCHAR) AS CUSTCODE,
        TRY_CAST(SERVICE_CODE AS VARCHAR) AS SERVICE_CODE,
        TRY_CAST(SERVICE_STATUS AS VARCHAR) AS SERVICE_STATUS,
        TRY_CAST(PARAMETER_DATE AS VARCHAR) AS PARAMETER_DATE,
        TRY_CAST("INTERVAL" AS VARCHAR) AS "INTERVAL",
        TRY_CAST(PARAMETER_NAME AS VARCHAR) AS PARAMETER_NAME,
        TRY_CAST(DESCRIPTION AS VARCHAR) AS DESCRIPTION,
        TRY_CAST(VALUE AS VARCHAR) AS VALUE,
        TRY_CAST(SERVICE_NAME AS VARCHAR) AS SERVICE_NAME,
        TRY_CAST(PRICE AS VARCHAR) AS PRICE,
        TRY_CAST('{run_date_formatted}' AS DATE) AS RUN_DATE
    FROM read_csv_auto('{billing_path}', ignore_errors=True, delim='|')
""")

con.execute(f"""
    CREATE OR REPLACE TABLE sdsl_speed AS
    WITH ranked AS (
        SELECT
            bn.sid,
            bn.network_status,
            bn.billing_status,
            n.MGT_VRF,
            n.PE_Speed,
            n.PE_Speed_Rule_Status,
            row_number() OVER (
                PARTITION BY bn.sid
                ORDER BY
                    strptime(n.Entry_Date, '%d/%m/%Y') DESC,  -- was n.parameter_date (didn't exist) -- VERIFY format
                    substr(n.actual_speed, 1, strpos(n.actual_speed, 'M') - 1) DESC
            ) AS r,
            CASE
                WHEN upper(n.actual_speed) LIKE '%MBPS%' THEN replace(replace(upper(n.actual_speed), 'MBPS', 'M'), ' ', '')
                WHEN upper(n.actual_speed) LIKE '%KBPS%' THEN replace(replace(upper(n.actual_speed), 'KBPS', 'K'), ' ', '')
                WHEN upper(n.actual_speed) LIKE '%GBPS%' THEN replace(replace(upper(n.actual_speed), 'GBPS', 'G'), ' ', '')
                WHEN n.actual_speed LIKE '%-%'          THEN substr(n.actual_speed, 1, strpos(n.actual_speed, '-') - 1)
                WHEN n.actual_speed IS NULL             THEN '0'
                ELSE n.actual_speed
            END AS network_speed,
            CASE
                WHEN upper(b.value) LIKE '%MBPS%' THEN replace(replace(upper(b.value), 'MBPS', 'M'), ' ', '')
                WHEN upper(b.value) LIKE '%KBPS%' THEN replace(replace(upper(b.value), 'KBPS', 'K'), ' ', '')
                WHEN upper(b.value) LIKE '%GBPS%' THEN replace(replace(upper(b.value), 'GBPS', 'G'), ' ', '')
                ELSE b.value
            END AS billing_speed
        FROM bscs_sdsl_vs_network bn
        LEFT JOIN bscs_sdsl b
            ON bn.sid = b.sid
           AND b.run_date = TRY_CAST('{run_date_formatted}' AS DATE)
           AND upper(b.parameter_name) = 'SPEED'
        LEFT JOIN sdsl_network n
            ON bn.sid = n.sid
           AND (upper(n.comments) NOT LIKE '%SHUT%' OR n.comments IS NULL)
           AND (n.vrf IS NOT NULL OR (n.vrf IS NULL AND upper(n.service_type) = 'LAYER 2'))
           AND (n.mngt_vrf = '0' OR n.mngt_vrf = '#N/A')  -- CONFIRM: sid_rules uses 'l2'/'l3' for MGT_VRF — may need to change
           AND upper(n.actual_speed) NOT LIKE 'SL%'
           AND upper(n.actual_speed) NOT LIKE 'ST%'
        WHERE bn.network_status = 'ACTIVE'
          AND bn.billing_status = 'A'
          AND bn.run_date = TRY_CAST('{run_date_formatted}' AS DATE)
    ),
    dedup AS (
        SELECT sid, network_status, billing_status, MGT_VRF, PE_Speed, PE_Speed_Rule_Status, network_speed, billing_speed
        FROM ranked
        WHERE r = 1
    ),
    converted AS (
        SELECT
            sid, network_status, billing_status, MGT_VRF, PE_Speed, PE_Speed_Rule_Status, network_speed, billing_speed,
            CASE
                WHEN upper(network_speed) LIKE '%G%' THEN TRY_CAST(trim(substr(network_speed, 1, strpos(upper(network_speed), 'G') - 1)) AS DOUBLE) * 1024 * 1024
                WHEN upper(network_speed) LIKE '%M%' THEN TRY_CAST(trim(substr(network_speed, 1, strpos(upper(network_speed), 'M') - 1)) AS DOUBLE) * 1024
                WHEN upper(network_speed) LIKE '%K%' THEN TRY_CAST(trim(substr(upper(network_speed), 1, strpos(upper(network_speed), 'K') - 1)) AS DOUBLE)
                WHEN network_speed = '0' THEN 0
            END AS new_network_speed,
            CASE
                WHEN upper(billing_speed) LIKE '%G%' THEN TRY_CAST(trim(substr(billing_speed, 1, strpos(upper(billing_speed), 'G') - 1)) AS DOUBLE) * 1024 * 1024
                WHEN upper(billing_speed) LIKE '%M%' THEN TRY_CAST(trim(substr(billing_speed, 1, strpos(upper(billing_speed), 'M') - 1)) AS DOUBLE) * 1024
                WHEN upper(billing_speed) LIKE '%K%' THEN TRY_CAST(trim(substr(billing_speed, 1, strpos(upper(billing_speed), 'K') - 1)) AS DOUBLE)
            END AS new_billing_speed
        FROM dedup
    ),
    diffed AS (
        SELECT
            sid, network_status, billing_status, MGT_VRF, PE_Speed, PE_Speed_Rule_Status, network_speed, billing_speed,
            CASE
                WHEN (trim(substr(upper(network_speed), strpos(upper(network_speed), 'G'))) = 'G'
                      OR trim(substr(upper(network_speed), strpos(upper(network_speed), 'M'))) = 'M'
                      OR trim(substr(upper(network_speed), strpos(upper(network_speed), 'K'))) = 'K')
                 AND (trim(substr(upper(billing_speed), strpos(upper(billing_speed), 'G'))) = 'G'
                      OR trim(substr(upper(billing_speed), strpos(upper(billing_speed), 'M'))) = 'M'
                      OR trim(substr(upper(billing_speed), strpos(upper(billing_speed), 'K'))) = 'K')
                THEN new_billing_speed - new_network_speed
            END AS d
        FROM converted
    )
    SELECT
        sid                  AS "Sid",
        network_status       AS "Network_status",
        billing_status       AS "Billing_status",
        MGT_VRF              AS "MGT_VRF",
        PE_Speed             AS "PE_Speed",
        PE_Speed_Rule_Status AS "PE_Speed_Rule_Status",
        network_speed        AS "Network_speed",
        billing_speed        AS "Billing_Speed",
        CASE
            WHEN d > 0 THEN 'BILLING HIGHER'
            WHEN d < 0 THEN 'NETWORK HIGHER'
            WHEN d = 0 THEN 'MATCHING'
            ELSE 'CANNOT IDENTIFY'
        END AS "Billing_Vs_Network",
        TRY_CAST('{run_date_formatted}' AS DATE) AS "RUN_DATE"
    FROM diffed
""")

print(con.sql("SELECT * FROM sdsl_speed LIMIT 20"))



print(con.sql(f"""
    SELECT bn.sid AS bn_sid, typeof(bn.sid) AS bn_sid_type,
           n.SID AS n_sid, typeof(n.SID) AS n_sid_type
    FROM read_csv_auto('{bscs_sdsl_vs_network_path}', ignore_errors=True) bn
    LEFT JOIN sdsl_network n ON upper(trim(CAST(bn.sid AS VARCHAR))) = upper(trim(n.SID))
    LIMIT 20
"""))



┌────────┐
│ loaded │
│ int64  │
├────────┤
│  10638 │
└────────┘

┌─────────┐
│ in_file │
│  int64  │
├─────────┤
│   10638 │
└─────────┘

┌────────────────┬─────────────┬─────────┬─────────┬─────────┬─────────┐
│  column_name   │ column_type │  null   │   key   │ default │  extra  │
│    varchar     │   varchar   │ varchar │ varchar │ varchar │ varchar │
├────────────────┼─────────────┼─────────┼─────────┼─────────┼─────────┤
│ SID            │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ PE Interface   │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ WAN IP         │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ MGT VRF        │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ VRF            │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ Actual Speed   │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ PE Describtion │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ Comments       │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ PE Speed       │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ Service Type   │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ Branch Name    │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ Entry Date     │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
├────────────────┴─────────────┴─────────┴─────────┴─────────┴─────────┤
│ 12 rows                                                    6 columns │
└──────────────────────────────────────────────────────────────────────┘

┌────────────────┬─────────────┬─────────┬─────────┬─────────┬─────────┐
│  column_name   │ column_type │  null   │   key   │ default │  extra  │
│    varchar     │   varchar   │ varchar │ varchar │ varchar │ varchar │
├────────────────┼─────────────┼─────────┼─────────┼─────────┼─────────┤
│ SID            │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ NETWORK_STATUS │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ BILLING_STATUS │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ IMPACT         │ VARCHAR     │ YES     │ NULL    │ NULL    │ NULL    │
│ RUN_DATE       │ TIMESTAMP   │ YES     │ NULL    │ NULL    │ NULL    │
└────────────────┴─────────────┴─────────┴─────────┴─────────┴─────────┘

┌─────────┬──────────────┐
│ MGT_VRF │ count_star() │
│ varchar │    int64     │
├─────────┼──────────────┤
│ l3      │         8321 │
│ NULL    │         1766 │
│ l2      │          551 │
└─────────┴──────────────┘

┌──────────┬──────────────────────┐
│ PE_Speed │ PE_Speed_Rule_Status │
│ varchar  │       varchar        │
├──────────┼──────────────────────┤
│ 1.2M     │ VALID: l3 only SID   │
│ 20M      │ VALID: l3 only SID   │
│ 70M      │ VALID: l3 only SID   │
│ 100M     │ VALID: l3 only SID   │
│ 20M      │ VALID: l2 only SID   │
│ 1G       │ VALID: l2 only SID   │
│ 200M     │ VALID: l3 only SID   │
│ 400M     │ VALID: l3 only SID   │
│ 4M       │ VALID: l3 only SID   │
│ 50M      │ VALID: l3 only SID   │
├──────────┴──────────────────────┤
│ 10 rows               2 columns │
└─────────────────────────────────┘

┌─────────┬─────────┐
│ bn_rows │ matched │
│  int64  │  int64  │
├─────────┼─────────┤
│   19761 │   10638 │
└─────────┴─────────┘
