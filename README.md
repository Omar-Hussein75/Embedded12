file_name=/apps/raidprd/raid-prj/scripts/logs/SDSL_Speed.log

$ORACLE_HOME/bin/sqlplus -s > $file_name <<END
RP7VFEPIDAT/RP7VFEPIDAT@RAIDPRD
INSERT INTO RP7VFEPIDAT.sdsl_speed
SELECT Sid, Network_status, BIlling_status,Network_speed, BIlling_Speed,
CASE WHEN d > 0 THEN 'BILLING HIGHER'
         WHEN d < 0 THEN 'NETWORK HIGHER'
         WHEN d =0      THEN 'MATCHING'
         ELSE 'CANNOT IDENTIFY'
         END AS BILLING_Vs_NETWORK
         ,to_Date($1,'YYYYMMDD') RUN_DATE
FROM (SELECT  Sid, Network_status, BIlling_status,Network_speed, BIlling_Speed,
CASE WHEN (REGEXP_REPLACE(SUBSTR(trim(upper(Network_speed)),instr(upper(Network_speed),'G')),'(^[[:space:]]*|[[:space:]]*$)') = 'G'
                        OR REGEXP_REPLACE(SUBSTR(trim(upper(Network_speed)),instr(upper(Network_speed),'M')),'(^[[:space:]]*|[[:space:]]*$)') = 'M'
                        OR REGEXP_REPLACE(SUBSTR(trim(upper(Network_speed)),instr(upper(Network_speed),'K')),'(^[[:space:]]*|[[:space:]]*$)') = 'K'
                  )
                AND(REGEXP_REPLACE(SUBSTR(trim(upper(billing_speed)),instr(upper(billing_speed),'G')),'(^[[:space:]]*|[[:space:]]*$)') = 'G'
                        OR REGEXP_REPLACE(SUBSTR(trim(billing_speed),instr(upper(billing_speed),'M')),'(^[[:space:]]*|[[:space:]]*$)') = 'M'
                        OR REGEXP_REPLACE(SUBSTR(trim(upper(billing_speed)),instr(upper(billing_speed),'K')),'(^[[:space:]]*|[[:space:]]*$)') = 'K'
                   )
                THEN new_billing_speed - new_network_speed
                END AS d
FROM(
Select sid,NETWORK_STATUS,BILLING_STATUS,NETWORK_SPEED,BILLING_SPEED,
CASE WHEN upper(network_speed) LIKE '%G%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(NETWORK_SPEED),1,instr(NETWORK_SPEED ,'G')-1),'(^[[:space:]]*|[[:space:]]*$)')) * 1024 * 1024
        WHEN upper(network_speed) LIKE '%M%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(NETWORK_SPEED),1,instr(NETWORK_SPEED ,'M')-1),'(^[[:space:]]*|[[:space:]]*$)')) * 1024
        WHEN upper(network_speed) LIKE '%K%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(Upper(NETWORK_SPEED)),1,instr(upper(NETWORK_SPEED),'K')-1),'(^[[:space:]]*|[[:space:]]*$)'))
        WHEN network_speed ='0'
        THEN 0
        END AS new_network_speed,
CASE WHEN upper(BILLING_SPEED) LIKE '%G%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(BILLING_SPEED),1,instr(BILLING_SPEED ,'G')-1),'(^[[:space:]]*|[[:space:]]*$)')) * 1024 * 1024
        WHEN upper(BILLING_SPEED) LIKE '%M%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(BILLING_SPEED),1,instr(BILLING_SPEED ,'M')-1),'(^[[:space:]]*|[[:space:]]*$)')) * 1024
        WHEN upper(BILLING_SPEED) LIKE '%K%'
        THEN TO_NUMBER(REGEXP_REPLACE(SUBSTR(trim(BILLING_SPEED),1,instr(BILLING_SPEED ,'K')-1),'(^[[:space:]]*|[[:space:]]*$)'))
        END AS new_BILLING_SPEED
FROM (SELECT bn.sid, bn.NETWORK_STATUS,bn.BILLING_STATUS,
row_number()OVER (PARTITION BY bn.sid ORDER BY to_date(PARAMETER_DATE,'DD/MM/YYYY')DESC,SUBSTR(ACTUAL_SPEED,1,instr(ACTUAL_SPEED ,'M')-1)desc) r,
CASE WHEN upper(ACTUAL_SPEED) LIKE '%MBPS%'
                                THEN REPLACE(REPLACE(upper(ACTUAL_SPEED),'MBPS','M'),' ','')
         WHEN upper(ACTUAL_SPEED) LIKE '%KBPS%'
                                THEN REPLACE(REPLACE(upper(ACTUAL_SPEED),'KBPS','K'),' ','')
        WHEN upper(ACTUAL_SPEED) LIKE '%GBPS%'
                                THEN REPLACE(REPLACE(upper(ACTUAL_SPEED),'GBPS','G'),' ','')
        WHEN n.ACTUAL_SPEED LIKE '%-%'
                THEN SUBSTR(n.ACTUAL_SPEED,1,(instr(n.ACTUAL_SPEED,'-'))-1)
         WHEN n.ACTUAL_SPEED IS NULL
                THEN '0'
         ELSE ACTUAL_SPEED
         END AS NETWORK_SPEED,
CASE WHEN upper(b.VALUE) LIKE '%MBPS%'
                                THEN REPLACE(REPLACE(upper(VALUE),'MBPS','M'),' ','')
                        WHEN upper(b.Value) LIKE '%KBPS%'
                                THEN REPLACE(REPLACE(upper(VALUE),'KBPS','K'),' ','')
                        WHEN upper(b.Value) LIKE '%GBPS%'
                                THEN REPLACE(REPLACE(upper(VALUE),'GBPS','G'),' ','')
                        ELSE value
                END AS BILLING_SPEED
FROM RP7VFEPIDAT.bscs_sdsl_vs_network bn
Left JOIN RP7VFEPIDAT.RD_T_BSCS_SDSL b
        ON bn.sid =b.sid AND b.RUN_DATE =to_Date($1,'YYYYMMDD') AND upper(b.PARAMETER_NAME) ='SPEED'
LEFT JOIN RP7VFEPIDAT.SDSL_NETWORK_STG n
        ON bn.sid = n.SID AND (upper(COMMENTS) NOT LIKE upper('%shut%') OR (COMMENTS IS NULL))
                AND (VRF IS NOT NULL OR (VRF IS NULL AND upper(SERVICE_TYPE) =upper('Layer 2')))
                AND ((MNGT_VRF ='0') OR (MNGT_VRF='#N/A'))
                AND  UPPER(ACTUAL_SPEED) NOT LIKE 'SL%' AND UPPER(ACTUAL_SPEED) NOT LIKE 'ST%'
WHERE bn.NETWORK_STATUS ='ACTIVE'AND BILLING_STATUS ='A'
AND bn.run_date =to_Date($1,'YYYYMMDD')
--AND bn.sid ='469/166'
--AND  UPPER(ACTUAL_SPEED) NOT LIKE 'SL%' AND UPPER(ACTUAL_SPEED) NOT LIKE 'ST%'
)
where r=1
--AND sid ='469/166'
)
--WHERE sid ='147702/30'
)
--WHERE sid IN (SELECT sid FROM RP7VFEPIDAT.SDSL_SPEED
--WHERE RUN_DATE =$1
--AND BILLING_VS_NETWORK ='CANNOT IDENTIFY')
;

COMMIT;
exit;
END
