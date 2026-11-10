import os
import sys

PROJECT_ROOT = os.path.abspath(
os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

import duckdb
import argparse
from datetime import datetime
from config.duckdb_connection import duckdb_connection
from utils.logger import get_logger
from utils.send_mail import send_mail
from utils.export_to_oracle import prepare_ctl
from utils.export_to_oracle import run_sqlldr
from utils.dump_vs_database import validate_loaded_data
import glob

def load_TE_data(run_date: str, con=None):

    logger = get_logger(
        flow="TE_data",
        log_dir=None,  
        run_date=run_date
    )

    try:
        if con is None:
            con = duckdb_connection()
            
        start_time = datetime.now()
        logger.info(f"###################################################")
        logger.info(f"###### TE_data started for run_date= {run_date} ######")
        logger.info(f"###################################################")
        run_date_formatted = f"{run_date[:4]}-{run_date[4:6]}-{run_date[6:]}"
        csv_path = f"/project/PLATFORM_INTEGRITY/exports/TE_data_{run_date}.csv"
        file_path = f"/RAIDdata/PI/TE_DATA/raw/TE_Numbers*.xlsx" ## Adjusting the code to include the rundate in the name, will be after alignment regarding naming with business


        files = glob.glob("/RAIDdata/PI/TE_DATA/raw/TE_Numbers*.xlsx")

        logger.info(f"Found {len(files)} TE_data files:")
        for file_path in files:
            logger.info(f"  {file_path}")

        if not files:
            raise FileNotFoundError(
                "No TE_data XLSX files found matching TE_Numbers*.xlsx"
            )

        queries = []

        for file_path in files:
            queries.append(f"""
                SELECT 
                    '20' || CAST(
                    TRY_CAST(
                    TRY_CAST(TRIM(CAST(TEL AS VARCHAR)) AS DECIMAL(20,0))
                    AS BIGINT
                     ) AS VARCHAR
                     ) AS MSISDN, 
                    TRY_CAST(Speed AS VARCHAR) AS TE_SPEED, 
                    CAST('{run_date_formatted}' AS DATE) AS RUN_DATE
                FROM read_xlsx('{file_path}', header=True)
            """)

        query = "\nUNION ALL\n".join(queries)
        
        con.execute(f""" CREATE OR REPLACE TABLE TE_data AS {query} """)

        count = con.execute("SELECT COUNT(*) FROM TE_data").fetchone()[0]
        logger.info(f"Total processed rows: {count}")

        if count == 0:
            logger.info(f"No TE_data data processed!")
            raise

        logger.info(f"Exporting TE_data_{run_date}.csv ..")
        con.execute(f""" COPY (SELECT RUN_DATE,  MSISDN, TE_SPEED  FROM TE_data) TO '{csv_path}' (DELIMITER ',', HEADER TRUE, QUOTE '"', ESCAPE '"'); """)
        logger.info(f"Inserting into PI.TE_data table ..")
        ctl_file = prepare_ctl("TE_data", run_date)
        run_sqlldr("TE_data",run_date, ctl_file=ctl_file)
        logger.info(f"Insertion done")

        logger.info(f"Validating inserted data ..")
        validate_loaded_data(
        flow_name="TE_data",
        run_date=run_date,
        expected_rows=count,
        logger=logger)
        logger.info("Inserted data validated successfully.")

        end_time = datetime.now()
        logger.info(f"TE_data finished")
        logger.info(f"TE_data total duration: {end_time - start_time}")
        send_mail(
                    subject="TE DATA Flow Finished",
                    content=f"""
                Run Date : {run_date}
                Rows      : {count}
                DB Validation : Passed
                Duration  : {end_time - start_time}""")
        return con.table("TE_data")
    except Exception as e:
        logger.exception(f"TE_data failed for run_date= {run_date} | error= {str(e)}")
        send_mail(
        subject="TE DATA Flow Failed",
        content=f"""
            Run Date : {run_date}
            Error:
            {str(e)}
            """)
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run_date", required=True)
    args = parser.parse_args()
    load_TE_data(run_date=args.run_date)
