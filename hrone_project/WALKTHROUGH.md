# HROne Attendance API Walkthrough

The API is implemented in [app/main.py](./app/main.py) using FastAPI, Pydantic, PyMongo, and MongoDB. The request and response contract is in [openapi.yaml](./openapi.yaml); the stored document shapes are in [DATA_MODEL.md](./DATA_MODEL.md). The application reads `MONGO_URI` and `MONGO_DB` from the environment, with `.env` support for local development. Environment variables take precedence. MongoDB connectivity and index-creation errors are not hidden.

## Request flow and attendance rules

Employee codes are the public identity; MongoDB `_id` values are never serialized. Employee creation relies on a database unique index, not a prior existence check. Epoch-millisecond inputs are strict integers converted to UTC datetimes; punch instants are truncated to whole seconds before business calculations and storage. Attendance dates are derived in IST, with early-morning punches for overnight shifts attributed to the preceding shift date.

Punch-in computes late minutes using the strict ten-minute grace boundary. Punch-out finds the most recent eligible punch-in, validates its time window, computes rounded work hours, overtime, and half-day status, then closes the record with a conditional atomic update. PATCH recalculates all derived values and uses a compare-against-read-state filter with an atomic `$push` of one audit entry. A competing correction cannot silently replace prior history.

## Indexes

All indexes are created idempotently in the FastAPI startup lifespan:

| Collection | Index | Purpose |
|---|---|---|
| `employees` | Unique `{emp_code: 1}` | Enforces employee identity and prevents duplicate employee creation. |
| `employees` | `{department: 1, joined_on: 1}` | Department-filtered eligible-headcount lookup, including trend-day headcount. |
| `employees` | `{joined_on: 1}` | Month-end eligible-employee scans when department is not specified. |
| `attendance_logs` | Unique `{emp_code: 1, date: 1}` | Enforces one record per employee attendance date and makes concurrent punch-in safe. |
| `attendance_logs` | `{date: -1, emp_code: 1}` | Supports attendance date-range listing and its date/employee sort. |
| `attendance_logs` | `{emp_code: 1, punch_in: -1}` | Finds an employee's most recent punch-in eligible for punch-out. |
| `attendance_logs` | `{status: 1, date: -1, emp_code: 1}` | Supports status-filtered attendance listing with the required sort. |

The unique constraints are correctness guarantees, not just performance hints. Startup stops with a visible error if MongoDB cannot create a required index.

## Analytics pipelines

### Employee monthly

`employee_monthly_pipeline` first matches one employee and the requested inclusive month bounds, then groups the stored status and derived values. It counts weekday present records only, gives half-day records a value of `0.5`, and retains weekend late/overtime contributions. Working days are counted from the later of the month start and `joined_on`. The attendance percentage uses decimal half-up rounding to four places; it is null when there are no working days.

### Department summary

`department_summary_pipeline` starts from eligible employees and looks up their attendance logs. Starting from employees preserves headcount for people with no logs. `joined_on <= month_end` implements the month-end headcount rule, and a department with no eligible employees cannot create an output group. The aggregation groups by department and calculates weekday present days, late records and minutes, leave records, on-duty records, and a record-weighted average of completed present-status work hours. Results are sorted by department.

### Late leaderboard

`late_leaderboard_pipeline` filters the month, totals late minutes and late records by employee, removes employees with no positive late total, and joins employee name and department. Optional department filtering is applied before ranking. `$setWindowFields` with `$rank` implements competition ranking, so tied totals share a rank and later positions are skipped. The rank cutoff is applied after ranking, which retains all ties at the cutoff; a final sort orders equal totals by employee code.

### Department trend

`department_trend_pipeline` starts with the indexed attendance-date range, joins employee documents to restrict records to the requested department, and groups by a parsed BSON UTC-midnight date. The parsed values and explicit densify bounds are all BSON dates, as required by `$densify`.

The pipeline unions a single department employee as a zero-count seed at the first requested date. This gives `$densify` an anchor even when that department has no attendance logs in the requested period. `$densify` then creates every calendar day through the inclusive end date. For each day, a correlated employee lookup counts employees whose `joined_on` is on or before that date. The pipeline calculates the working-day flag, presence count (including half days), late count, and an attendance rate only for working days with positive headcount. Decimal128 arithmetic and floor-after-adding-one-half implement four-place half-up rounding. Finally, `$setWindowFields` computes the mean of non-null rates across the current date and up to six preceding dates. The handler returns the public `items` projection rather than internal aggregation rows.

## Explain endpoint

`/admin/explain/{endpoint}` sends MongoDB an `explain` command with `executionStats` verbosity. It builds the same query filters, sort, page limits, or aggregation pipeline used by the corresponding endpoint and returns MongoDB's explain document with nested BSON values converted to JSON-compatible values (timestamps use `time` and `inc`; Decimal128 and binary values retain Extended-JSON-style metadata). Supported names are `attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, and `department_trend`. Required query parameters are validated before the command is sent. On a sufficiently populated MongoDB dataset, inspect the plan for `IXSCAN` and verify that the relevant query has not fallen back to `COLLSCAN`; explain plans can vary on tiny collections because MongoDB's cost-based planner may prefer a collection scan.

## Tests and supported runtime

The database-backed tests in [tests/test_basic.py](./tests/test_basic.py) use a real MongoDB instance. They seed the actual project `sample_data` files into an isolated, uniquely named database, then cover endpoint status codes, input validation, pagination, R1-R10 boundaries, overnight shifts, monthly and department calculations, tied leaderboard ranks, empty trend dates, explain calls, audit history, and concurrent writes. The BSON explain-serialization regression test stubs only MongoDB's explain command result and does not need a database. MongoDB 6.0 or later is required for `$densify`, `$unionWith`, and `$setWindowFields`.

## Interview questions and answers

1. **Why is `emp_code` separate from MongoDB `_id`?**  
   `emp_code` is the stable, customer-assigned identifier exposed by the API. `_id` is an internal storage key and must not leak into the HTTP contract.

2. **Why enforce employee uniqueness with a database index?**  
   An application-level read-then-insert has a race. The unique index makes the constraint atomic across concurrent requests.

3. **How does concurrent punch-in behave?**  
   Both requests attempt the insert; the unique `(emp_code, date)` index permits one and raises `DuplicateKeyError` for the other, which maps to HTTP 409.

4. **How is punch-out made concurrency-safe?**  
   The update matches the record id and `punch_out: null`. Only the first update matches; another request receives 409.

5. **How are concurrent corrections protected?**  
   The PATCH update includes the old status and punch timestamps in its selector and atomically `$push`es its history entry. A stale write matches zero records and returns 409.

6. **Why are correction changes and history written in one update?**  
   The `$set` of corrected values and `$push` of the audit entry are atomic on one MongoDB document, so a successful correction cannot persist without its history entry.

7. **Why store instants as BSON datetimes?**  
   BSON datetimes support time-range queries and sorting. The HTTP boundary still uses epoch milliseconds, avoiding local-time interpretation.

8. **Why truncate punch instants to whole seconds?**  
   R1 requires calculations and stored punch values to use second precision, so millisecond tails cannot change attendance date or derived values.

9. **How is IST represented?**  
   The code uses a fixed UTC+05:30 timezone for calendar and shift calculations, while MongoDB instants remain UTC.

10. **How is an overnight attendance date selected?**  
    When `shift_end <= shift_start`, a punch-in whose IST time is earlier than `shift_end` is assigned to the previous calendar date.

11. **What does the late grace boundary mean?**  
    A punch exactly ten minutes after shift start is not late. A punch one second later is late and its whole-minute count is floored from shift start.

12. **Why use `Decimal` for work-hour rounding?**  
    Python's built-in `round` uses ties-to-even and binary floats can perturb decimal boundaries. Decimal half-up gives the required result.

13. **How is the half-day decision made?**  
    It uses the already rounded work-hours value; `4.50` is not a half day, while a rounded value below it is.

14. **Why does monthly attendance exclude weekend presence but include weekend lateness?**  
    R7 defines `present_days` using weekdays only, while explicitly retaining weekend late and overtime values.

15. **How can department headcount include employees with no attendance rows?**  
    The summary pipeline begins at the eligible employee collection and left-looks-up logs. Employees with no matching logs remain in the group.

16. **How is the department work-hour average defined?**  
    It is a record-weighted average over present-status records with a non-null `work_hours`, not an average of per-employee averages.

17. **How does the leaderboard represent ties?**  
    `$rank` implements competition ranking. Equal totals share a rank and the next rank is skipped, such as `1, 2, 2, 4`.

18. **Why apply the leaderboard limit after ranking?**  
    Filtering by rank keeps every employee tied at or above the cutoff rank, even if this returns more rows than the requested limit.

19. **Why does the trend convert date strings to BSON dates?**  
    `$densify` requires date or numeric values. Converting ISO calendar labels to BSON dates makes them type-compatible with the date bounds.

20. **How does the trend handle a period with no attendance logs?**  
    `$unionWith` obtains one employee from the selected department and contributes a zero-count anchor date before densification.

21. **How is trend headcount time-sensitive?**  
    A per-day lookup counts department employees whose `joined_on` is no later than that specific calendar day.

22. **What does a missing weekday mean in trend output?**  
    It has `present_count: 0`; if headcount is positive, its attendance rate is `0.0`. Weekend or zero-headcount rates are null.

23. **How is the seven-day moving average defined?**  
    A documents window includes the current date and six preceding dates in the requested range. Null rates are ignored by `$avg`.

24. **Why round the rate before calculating the moving average?**  
    Each reported `attendance_rate` is a four-place value; averaging those reported values keeps the moving average aligned with the response's rate semantics.

25. **Why create a date-first listing index?**  
    The attendance endpoint sorts by descending date and ascending employee code and supports inclusive date-range scans.

26. **Why use a compound employee/date attendance index?**  
    It both guarantees one attendance record per employee/date and supports employee-month queries using a leading employee-code equality.

27. **Why can an explain plan choose `COLLSCAN` on a tiny test database?**  
    For very small collections, MongoDB may estimate a collection scan to be cheaper. The index choice should be evaluated on representative data with execution statistics.

28. **Why not calculate all analytics in Python?**  
    MongoDB can filter and aggregate near the indexed data, avoiding loading large collections into application memory and preserving the assignment's aggregation requirements.

29. **Why return filtered `total` separately from paginated items?**  
    Clients need the size of the full filtered set for pagination; counting only the current page would produce incorrect page controls.

30. **Why does the test fixture use a unique database name?**  
    It isolates test data from the user's normal database and from other concurrent test runs while still exercising a real MongoDB server.

## Windows commands

Run these commands from the project root in PowerShell. Python 3.12 is used here; Python 3.11+ is supported.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
python -m uvicorn app.main:app --port 8000
```

The MongoDB service must already be installed and running, or `MONGO_URI` must point to an accessible MongoDB Atlas cluster. In another PowerShell window, run:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
python -m pytest -v
```

To prepare a clean ZIP without a virtual environment, `.env`, caches, or local database files, run from the project root:

```powershell
@'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path.cwd()
archive = root / "submission.zip"
selected = ["app", "tests", "sample_data", "README.md", "WALKTHROUGH.md",
            "REVIEW.md", "DECISIONS.md", "DATA_MODEL.md", "openapi.yaml",
            "requirements.txt", "sample_seed.py"]
excluded = {"__pycache__", ".pytest_cache", ".venv", "venv", ".git"}
with ZipFile(archive, "w", ZIP_DEFLATED) as package:
    for name in selected:
        path = root / name
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in files:
            if file.is_file() and not excluded.intersection(file.parts) \
                    and file.suffix != ".pyc" and file.name != ".env":
                package.write(file, file.relative_to(root).as_posix())
print(f"Created {archive}")
'@ | python -
```
