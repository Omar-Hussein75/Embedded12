print(con.sql("""
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE upper(comments) NOT LIKE '%SHUT%' OR comments IS NULL) AS pass_comments,
        count(*) FILTER (WHERE vrf IS NOT NULL OR upper(service_type) = 'LAYER 2')     AS pass_vrf,
        count(*) FILTER (WHERE MGT_VRF = '0' OR MGT_VRF = '#N/A')                      AS pass_mgt_vrf,
        count(*) FILTER (WHERE upper(actual_speed) NOT LIKE 'SL%'
                           AND upper(actual_speed) NOT LIKE 'ST%')                     AS pass_speed_prefix
    FROM sdsl_network
"""))
