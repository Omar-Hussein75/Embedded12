import duckdb

con = duckdb.connect("my.db")  # or an in-memory connection

run_date_param = "20261104"  # equivalent to Oracle's $1 positional parameter

con.execute(
    """
    CREATE OR REPLACE TABLE sdsl_speed AS
    WITH ranked AS (
        SELECT
            bn.sid,
            bn.network_status,
            bn.billing_status,
            row_number() OVER (
                PARTITION BY bn.sid
                ORDER BY
                    strptime(n.parameter_date, '%d/%m/%Y') DESC,
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
        LEFT JOIN rd_t_bscs_sdsl b
            ON bn.sid = b.sid
           AND b.run_date = strptime(?, '%Y%m%d')::DATE
           AND upper(b.parameter_name) = 'SPEED'
        LEFT JOIN sdsl_network_stg n
            ON bn.sid = n.sid
           AND (upper(n.comments) NOT LIKE '%SHUT%' OR n.comments IS NULL)
           AND (n.vrf IS NOT NULL OR (n.vrf IS NULL AND upper(n.service_type) = 'LAYER 2'))
           AND (n.mngt_vrf = '0' OR n.mngt_vrf = '#N/A')
           AND upper(n.actual_speed) NOT LIKE 'SL%'
           AND upper(n.actual_speed) NOT LIKE 'ST%'
        WHERE bn.network_status = 'ACTIVE'
          AND bn.billing_status = 'A'
          AND bn.run_date = strptime(?, '%Y%m%d')::DATE
    ),
    dedup AS (
        SELECT sid, network_status, billing_status, network_speed, billing_speed
        FROM ranked
        WHERE r = 1
    ),
    converted AS (
        SELECT
            sid, network_status, billing_status, network_speed, billing_speed,
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
            sid, network_status, billing_status, network_speed, billing_speed,
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
        sid            AS "Sid",
        network_status AS "Network_status",
        billing_status AS "Billing_status",
        network_speed  AS "Network_speed",
        billing_speed  AS "Billing_Speed",
        CASE
            WHEN d > 0 THEN 'BILLING HIGHER'
            WHEN d < 0 THEN 'NETWORK HIGHER'
            WHEN d = 0 THEN 'MATCHING'
            ELSE 'CANNOT IDENTIFY'
        END AS "Billing_Vs_Network",
        strptime(?, '%Y%m%d')::DATE AS "RUN_DATE"
    FROM diffed
    """,
    [run_date_param, run_date_param, run_date_param],
)

print(con.sql("SELECT * FROM sdsl_speed LIMIT 20"))
