# TE Invoice Validation Flow (DuckDB)

This is a DuckDB version of the Oracle job that loads `RP7VFEPIDAT.RD_T_TE_INVOICE_VALIDATION`.
For every TE subscriber on the run date, it compares the subscriber's status in
**RADIUS**, **BSCS (billing)** and **IN (charging)**. It then gives each subscriber a
`CASE_COMMENT` (`ACTIVE`, `SUSPENDED`, `MISMATCH`, …) and adds the PCRF status, the
quota name and the speeds.

The logic is the same as the original Oracle SQL. Each Oracle table is replaced by
its CSV export, and `QUOTA_NAME_LKP` is replaced by the output of the
**quota lookup flow** (`quota_lkb_<run_date>.csv`).

---

## Files

| File | Purpose |
|------|---------|
| `te_invoice_validation/te_invoice_validation.py` | The flow itself |
| `te_invoice_validation/README.md` | This document |

---

## Dependencies

- **Python** 3.9 or later
- **DuckDB** Python package, tested on 1.1.x and 2.0:
  ```bash
  pip install duckdb
  ```
- **`config/duckdb_connection.py`** at the project root, providing
  `duckdb_connection()`. It's the same helper the quota lookup flow uses. The
  script adds the project root (the parent of `te_invoice_validation/`) to
  `sys.path`.
- **TE data flow** (`te_data/te_data.py --run_date <YYYYMMDD>`) must already
  have run for the same date. It produces `TE_data_<run_date>.csv`.
- **Quota lookup flow** must already have run for the same date (see
  [Inputs](#inputs)).

Expected layout:

```
<project root>/
├── config/
│   └── duckdb_connection.py
└── te_invoice_validation/
    ├── te_invoice_validation.py
    └── README.md
```

---

## Setup

1. Install DuckDB (`pip install duckdb`).
2. Make sure `config/duckdb_connection.py` exists at the project root.
3. Check the paths at the top of `te_invoice_validation.py` and adjust them to
   your server:

   | Variable | Default |
   |----------|---------|
   | `te_path` | `/project/PLATFORM_INTEGRITY/exports/TE_data_<run_date>.csv` (output of the TE data flow) |
   | `bscs_path` | `/RAIDdata/PI/PI_PRJ/exports/bscs_profiles_adsl_<file_date>.csv` |
   | `pcrf_path` | `/RAIDdata/PI/PI_PRJ/exports/pcrf_<file_date>.csv` |
   | `radius_path` | `/RAIDdata/PI/PI_PRJ/exports/radius_<file_date>.csv` |
   | `charging_path` | `/RAIDdata/PI/PI_PRJ/exports/charging_adsl_<file_date>.csv` |
   | `quota_path` | `/project/PLATFORM_INTEGRITY/exports/quota_lkb_<run_date>.csv` |
   | `export_path` | `/project/PLATFORM_INTEGRITY/exports/te_invoice_validation_<run_date>.csv` |

   > The **RADIUS** file name is a placeholder that follows the naming used by
   > the other exports. Change it to the real file name.

4. `file_date` (the date stamp in the file names) defaults to `run_date`. Set it
   by hand only if your snapshot files carry a different date.

---

## Execution

1. Run the TE data flow and the quota lookup flow for the date first, so that
   `TE_data_<run_date>.csv` and `quota_lkb_<run_date>.csv` exist.
2. Run this flow from the project root. Pass the run date as `YYYYMMDD`; it
   replaces the `$1` of the original shell script:

   ```bash
   python te_invoice_validation/te_invoice_validation.py 20260928
   ```

   If no date is given, the default `run_date` set in the script is used.

The script:

1. Checks that every input file exists. It stops with a clear message if one is
   missing, including the quota file.
2. Builds the result once into a DuckDB temp table.
3. Applies the original follow-up `UPDATE`:
   `CUSTOMER_TYPE = 'NA'` where it is NULL.
4. Prints the result, the row count and a breakdown by `CASE_COMMENT`.
5. Writes the CSV to `export_path`.

---

## Inputs

All files are CSV files with a header row and `,` as the delimiter. Every column
is read as text, so that MSISDNs keep their leading zeros and join the same way
across all sources. `RUN_DATE` may be stored as `20260928` or `2026-09-28`.

| Source (Oracle table) | Export | Filter applied | Columns used |
|-----------------------|--------|----------------|--------------|
| `RD_T_TE_DATA_BASE_HISTORICAL` | `te_path` (TE data flow output) | `RUN_DATE` = run date | `RUN_DATE`, `MSISDN`, `TE_SPEED` |
| `PFM_BSCS_ADSL` | `bscs_path` | `RUN_DATE` = run date | `RUN_DATE`, `MSISDN`, `STATUS`, `TYPE` |
| `PFM_PCRF_FILTERED` | `pcrf_path` | `RUN_DATE` = run date | `RUN_DATE`, `MSISDN`, `CUSTOMER_STATUS` |
| `PFM_RADIUS` | `radius_path` | `RUN_DATE` = run date | `RUN_DATE`, `MSISDN`, `STATUS` |
| `RD_T_IN_ADSL_HISTORICAL` | `charging_path` | `PACKAGE_TYPE = 'DSL Status'` and `RUN_DATE` = run date | `RUN_DATE`, `MSISDN`, `STATUS`, `PACKAGE_TYPE` |
| `QUOTA_NAME_LKP` | `quota_path` (quota lookup flow output) | `TIME` = run date | `TIME`, `MSISDN`, `QUOTA_NAME` |

> **Check these columns:** the BSCS, PCRF and charging files must contain the
> columns listed above. In particular they need `STATUS`, `CUSTOMER_STATUS` and
> `PACKAGE_TYPE`. If the exports used by the quota flow don't have them, point
> these paths at the `PFM_BSCS_ADSL`, `PFM_PCRF_FILTERED` and
> `RD_T_IN_ADSL_HISTORICAL` exports instead.

---

## Output

`te_invoice_validation_<run_date>.csv` (target: `RD_T_TE_INVOICE_VALIDATION`),
one row per distinct combination, ordered by `TE_MSISDN`:

| Column | Meaning |
|--------|---------|
| `RUN_DATE` | Run date |
| `TE_MSISDN` | Subscriber MSISDN from TE |
| `RADIUS_STATUS` | RADIUS status: `0`→`DEACTIVE`, `1`→`ACTIVE`, `2`→`SUSPENDED`, any other value is uppercased, missing → `Not_Exist` |
| `BILLING_STATUS` | BSCS `STATUS` as is, missing → `Not_Exist` |
| `CHARGING_STATUS` | IN `STATUS` uppercased, missing → `Not_Exist` |
| `CASE_COMMENT` | Result of the RADIUS/BILLING/CHARGING comparison (see below) |
| `CUSTOMER_TYPE` | BSCS `TYPE`, `NA` when missing |
| `PCRF_STATUS` | PCRF status: `0`→`DEACTIVE`, `1`→`ACTIVE`, `2`→`SUSPENDED`, `3`→`ON-HOLD`, any other value is uppercased, missing stays empty |
| `QUOTA_NAME` | Quota name from the quota lookup flow |
| `TE_SPEED` | Speed from TE |
| `SPEED` | First `<number>M` found in `QUOTA_NAME` (e.g. `140M`), empty if none |

### CASE_COMMENT rules

| RADIUS | BILLING | CHARGING | CASE_COMMENT |
|--------|---------|----------|--------------|
| ACTIVE | ACTIVE | Not_Exist | ACTIVE |
| ACTIVE | Not_Exist | ACTIVE | ACTIVE |
| ACTIVE | ACTIVE | ACTIVE | ACTIVE |
| ACTIVE | SUSPENDED | SUSPENDED | SUSPENDED |
| ACTIVE | SUSPENDED | ACTIVE | SUSPENDED |
| ACTIVE | ACTIVE | SUSPENDED | SUSPENDED |
| ACTIVE | SUSPENDED | Not_Exist | SUSPENDED |
| ACTIVE | Not_Exist | SUSPENDED | SUSPENDED |
| Not_Exist | Not_Exist | Not_Exist | Not_Exist |
| DEACTIVE | Not_Exist | Not_Exist | Not_Exist/DEACTIVE |
| DEACTIVE | SUSPENDED | Not_Exist | MISMATCH |
| ACTIVE | Not_Exist | Not_Exist | MISMATCH |
| DEACTIVE | ACTIVE | Not_Exist | MISMATCH |
| Not_Exist | ACTIVE | ACTIVE | MISMATCH |
| Not_Exist | ACTIVE | Not_Exist | MISMATCH |
| DEACTIVE | ACTIVE | SUSPENDED | MISMATCH |
| DEACTIVE | ACTIVE | ACTIVE | MISMATCH |
| DEACTIVE | Not_Exist | ACTIVE | MISMATCH |
| Not_Exist | SUSPENDED | Not_Exist | MISMATCH |
| DEACTIVE | Not_Exist | SUSPENDED | MISMATCH |
| *any other combination* | | | `_N` |

---

## How the Oracle features were translated

| Oracle | DuckDB |
|--------|--------|
| `NVL(a, b)` | `COALESCE(a, b)` |
| `DECODE(x, '0', …, default)` | `CASE x WHEN '0' THEN … ELSE x END` |
| `DECODE(PCRF.Customer_STATUS, 0, …)` (numeric) | `CASE TRY_CAST(CUSTOMER_STATUS AS DOUBLE) WHEN 0 …` |
| `REGEXP_SUBSTR(q, '[0-9]+M', 1, 1)` | `NULLIF(regexp_extract(q, '[0-9]+M'), '')` (Oracle returns NULL when nothing matches) |
| `run_date = to_number('$1')` / `to_date('$1','YYYYMMDD')` | Run date parsed from either `YYYYMMDD` or `YYYY-MM-DD` and compared to the run date |
| `''` is NULL in Oracle | Empty or blank values are turned into NULL |
| `INSERT …; COMMIT;` then `UPDATE … SET CUSTOMER_TYPE='NA' WHERE CUSTOMER_TYPE IS NULL` | Temp table, the same `UPDATE`, then CSV export |
| Outer `WHERE run_date = to_date('$1')` | Applied directly to the TE source. It only filters TE columns, so the result is the same |

Text values are trimmed before they are compared or joined. This cleans up
padding that sometimes appears in CSV exports. Otherwise the CASE/status logic is
unchanged, and string comparisons are case-sensitive as in Oracle. For example,
`BILLING_STATUS` is not uppercased.
