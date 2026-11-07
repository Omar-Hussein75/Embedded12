print(con.sql("""
    SELECT
        count(*)                                                     AS total_rows,
        count(Actual_Speed)                                          AS has_actual_speed,
        count(*) FILTER (WHERE trim(Actual_Speed) = '')              AS empty_string,
        count(*) FILTER (WHERE upper(comments) LIKE '%SHUT%')        AS shut_comments,
        count(*) FILTER (WHERE upper(Actual_Speed) LIKE 'SL%'
                            OR upper(Actual_Speed) LIKE 'ST%')       AS sl_st_prefix
    FROM sdsl_network
"""))

print(con.sql("""
    SELECT Actual_Speed, PE_Speed, MGT_VRF, count(*) AS n
    FROM sdsl_network
    GROUP BY ALL
    ORDER BY n DESC
    LIMIT 20
"""))

print(con.sql("""
    SELECT Network_speed, count(*) AS n
    FROM sdsl_speed
    GROUP BY 1 ORDER BY n DESC LIMIT 10
"""))
