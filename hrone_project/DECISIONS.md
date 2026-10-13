# DECISIONS.md

About 150-200 words in total - plain, natural language answering the five questions from the supplied template:

1. **Which indexes were created and what queries or guarantees do they support? Which index was considered and rejected?**

   We created four indexes: (1) a unique compound index on `(emp_code, date)` on `attendance_logs` which guarantees exactly one punch-in per employee per day and speeds up punch-out lookups, monthly analytics, and regularization by the natural key; (2) an index on `(date DESC, emp_code ASC)` which supports the attendance list sort+filter, date-range scans, and the trend densify operation; (3) an index on `(emp_code, punch DESC)` which supports punch-out record selection by finding the most recent punch-in efficiently; (4) an index on `(status, date DESC, emp_code)` which supports the attendance list filtered by status with the required sort order. An index on `(emp_code, joined_on)` on employees was also created to support R9 headcount queries. We considered adding a `(status, emp_code, date)` index on attendance_logs but rejected it because the attendance list's default sort and the explain checks are already satisfied by the `(status, date, emp_code)` compound, and the extra write overhead wasn't justified for the query patterns used.

2. **What exactly happens when two identical punch-in requests arrive simultaneously?**

   Both requests arrive at the `insert_one` step. The unique compound index on `(emp_code, date)` causes the second request to raise a `DuplicateKeyError` because the `(emp_code, date)` pair already exists. The code catches `DuplicateKeyError` and returns HTTP 409 for the concurrent request, while the first request successfully inserts and returns 201. No check-then-insert race is needed because the database index atomically serializes the writes.

3. **What does the leaderboard return when two employees tie at the limit cutoff?**

   The leaderboard uses competition ranking (`1, 2, 2, 4`) via MongoDB's `$rank` window operator. When two employees tie on `total_late_minutes`, they receive the same rank. The `limit` parameter is applied **after** ranking, so if the cutoff rank is 2 and two employees tie at rank 2, both are included in the response even though `limit=2` was requested. The ordering within tied ranks uses `emp_code` ascending. Employees with zero total late minutes are excluded via a `$match` stage before the window calculation.

4. **How does the department summary count employees with no attendance records?**

   The department summary pipeline drives from the `employees` collection with a `$match` on `joined_on <= month_end` (R9). This ensures every eligible employee appears in the grouping, regardless of whether they have any attendance logs. The `$lookup` into `attendance_logs` with `preserveNullAndEmptyArrays: True` keeps employees with zero logs; their contribution to `present_days` is 0, to `late_count` is 0, etc. The `headcount` is computed as `$size` of the accumulated employee set per department, which naturally includes employees with no logs. Employees who joined after the reporting period are excluded by the initial `$match`.

5. **What would you change to support 100x the data?**

   The main bottleneck would be the department summary pipeline which currently drives from `employees` and looks up all logs per employee. To scale to 100x, we would restructure the pipeline to drive from `attendance_logs` matched by the reporting month (using the `(date, emp_code)` index), then `$lookup` employees for department and join date — this limits the initial scan to only the relevant month's records rather than all employee logs. Additionally, we would add granular sharding on `emp_code` and `date` across a MongoDB cluster, and add covering indexes that include all projected fields to reduce document fetches. The aggregation framework already processes 100k-record datasets efficiently; the primary change is reducing the scan scope at the top of the pipeline.