# Employee Attendance & Analytics API

## Project Overview

Backend for the HROne Software Engineer Trainee technical assignment: an Employee Attendance & Analytics API built with FastAPI + PyMongo + MongoDB. The service supports employee management, punch-in/punch-out, manual regularization with audit trails, and five analytics endpoints (employee monthly, department summary, late leaderboard, department trend, and query explanation).

## Technology Stack

- Python 3.11+
- FastAPI
- PyMongo
- MongoDB 6.0+ (MongoDB Atlas or local MongoDB)
- Pydantic v2
- python-dotenv
- Uvicorn
- Pytest for automated testing

## Prerequisites

- Python 3.11+.
- MongoDB 6.0 or newer, either an installed local MongoDB Community Server or MongoDB Atlas. Docker is not required.
- PowerShell on Windows (examples below).

## Environment Variable Configuration

The app reads `MONGO_URI` and `MONGO_DB` from environment variables. A local `.env` file is fine for development, but real environment variables always take priority. Never hard-code connection details.

Example `.env`:

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

## Installation

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## MongoDB Setup

**Installed local MongoDB Community Server:** Start the MongoDB Windows service from an elevated PowerShell window if it is not already running:

```powershell
Start-Service MongoDB
```

**MongoDB Atlas:** Set the connection string in the current PowerShell session (or configure it in the environment before launching):

```powershell
$env:MONGO_URI = "mongodb+srv://<user>:<password>@<cluster>/"
$env:MONGO_DB = "attendance_db"
```

## Sample-Data Seeding

```powershell
python sample_seed.py
```

This loads the sample employee and attendance records from `sample_data/` into your MongoDB collections (`employees`, `attendance_logs`). The grader's hidden dataset is much larger and contains cases they do not show (ties, weekend records, employees with no logs at all, people who join mid-month, and more). Passing on this sample proves nothing about the analytics.

## Application Startup

```powershell
uvicorn app.main:app --port 8000
```

The app must be ready (`GET /health` returns 200) within 20 seconds when MongoDB is available. All indexes are created idempotently at startup.

## Endpoint Overview

| Method | Endpoint | Summary |
|---|---|---|
| GET | `/health` | Readiness probe; 200 if MongoDB responds, else 503 |
| POST | `/employees` | Create employee; 409 on duplicate emp_code; 422 on invalid input |
| GET | `/employees` | List employees; department-filtered, paginated, sorted by emp_code |
| POST | `/attendance/punch-in` | Record a punch-in (R1, R2); race-safe via unique index |
| GET | `/attendance` | List attendance records; filter by emp_code/date/status; paginated |
| POST | `/attendance/punch-out` | Record a punch-out (R3-R5); works across midnight |
| PATCH | `/attendance/{emp_code}/{date}` | Manual regularization with audit trail (R1-R5) |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee (aggregation) |
| GET | `/analytics/departments/summary` | Department summary for a month (aggregation, cross-collection) |
| GET | `/analytics/leaderboard/late` | Late-comers ranking with ties (competition ranking 1,2,2,4) |
| GET | `/analytics/departments/{department}/trend` | Daily attendance trend with 7-day moving average |
| GET | `/admin/explain/{endpoint}` | MongoDB explain output for a named endpoint's main query |

## Test Commands

```powershell
python -m pytest -v
python sample_seed.py
```

The test suite uses a uniquely named database and requires a reachable MongoDB instance. It does not require or start Docker.

## Known Limitations

- The trend endpoint's date range is limited to 92 days (`to - from > 92 → 422`).
- The leaderboard `limit` applies after ranking; tied employees at the cutoff are all included.
- The department summary counts employees who joined on or before the month end (R9), even if they have zero attendance logs.
- All rounding uses Decimal half-up (R8); Python's built-in `round()` (banker's) is NOT used.
- The `punched_at` field rejects seconds-representable values (< 100000000000), floats, strings, and out-of-range values with 422.

## Submission Checklist

- [x] `app/main.py` — all application code in this one file
- [x] `requirements.txt` — all dependencies at repository root
- [x] `REVIEW.md` — defects found and fixes documented
- [x] `DECISIONS.md` — 5 questions answered (~150-200 words)
- [x] `README.md` — project overview, tech stack, setup, endpoints
- [ ] No `.env`, no secrets, no Dockerfile, no committed data dumps
- [ ] Repository is public, link submitted by deadline
