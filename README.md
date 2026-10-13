# 🕒 Employee Attendance & Analytics API

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=rounded&color=0:0F172A,45:1D4ED8,100:06B6D4&height=200&section=header&text=Attendance%20%26%20Analytics%20API&fontSize=32&fontColor=FFFFFF&animation=fadeIn&fontAlignY=40&desc=FastAPI%20%7C%20PyMongo%20%7C%20MongoDB&descSize=16&descAlignY=65" width="100%" alt="Employee Attendance and Analytics API banner" />
</p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=600&size=17&duration=3000&pause=900&color=0891B2&center=true&vCenter=true&width=700&lines=Reliable+Employee+Attendance+Management;Aggregation-Powered+Analytics;Audit-Trail+Based+Regularization;Race-Safe+Punch-In+Operations;Built+with+FastAPI+%2B+MongoDB" alt="Animated project description" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-API-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PyMongo-Driver-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="PyMongo" />
  <img src="https://img.shields.io/badge/MongoDB-6.0%2B-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB" />
  <img src="https://img.shields.io/badge/Pydantic-v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white" alt="Pydantic v2" />
  <img src="https://img.shields.io/badge/Pytest-Testing-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white" alt="Pytest" />
</p>

<p align="center">
  <b>A backend API for employee management, attendance tracking, manual regularization, and aggregation-powered workforce analytics.</b>
</p>

---

## 📌 Project Overview

The **Employee Attendance & Analytics API** was developed for the HROne Software Engineer Trainee technical assignment.

Built with **FastAPI, PyMongo, and MongoDB**, it provides employee management, punch-in/punch-out workflows, attendance regularization with audit trails, and five analytics endpoints.

The project emphasizes data validation, database indexing, race-safe attendance operations, aggregation pipelines, and predictable API responses.

## ✨ Core Features

<table>
  <tr>
    <td width="50%">
      <h3>👥 Employee Management</h3>
      Create and list employees with duplicate employee-code validation, department filtering, pagination, and sorting.
    </td>
    <td width="50%">
      <h3>🕒 Attendance Tracking</h3>
      Record punch-in and punch-out events, including overnight shifts and attendance records across midnight.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>📝 Attendance Regularization</h3>
      Manually correct attendance through a PATCH endpoint with an audit trail.
    </td>
    <td width="50%">
      <h3>📊 Workforce Analytics</h3>
      Generate employee summaries, department statistics, late-comer rankings, and daily trends.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>⚡ Database Reliability</h3>
      Use idempotent index creation and a unique index for race-safe punch-in operations.
    </td>
    <td width="50%">
      <h3>🔎 Query Explainability</h3>
      Retrieve MongoDB explain output for the main query associated with a named endpoint.
    </td>
  </tr>
</table>

## 🏗️ System Architecture

<p align="center">
  <img src="https://mermaid.ink/img/pako:eNp1kE1rwzAMhv9KlBPHdhM7aSht0E4HDa2H3cGOxC5xU1vZ0NimQeT_3zG1sT1M8vH5PpIY0i5FQwM6V3qg8jB6Qp7yqBq1c4qWmY1q3pBqjZr3W2tQm4mQ2w2Q0g0tR2p0aWw" width="100%" alt="Architecture diagram placeholder for Employee Attendance and Analytics API" />
</p>

```mermaid
flowchart TD
    U["👤 API Client"] --> API["⚡ FastAPI Application"]
    API --> VAL["🛡️ Pydantic v2 Validation"]
    VAL --> EMP["👥 Employee Management"]
    VAL --> ATT["🕒 Attendance Operations"]
    VAL --> REG["📝 Regularization + Audit Trail"]
    VAL --> ANA["📊 Analytics Endpoints"]
    VAL --> EXP["🔎 Query Explain"]
    EMP --> DB[("🍃 MongoDB")]
    ATT --> DB
    REG --> DB
    ANA --> AGG["⚙️ MongoDB Aggregation Pipelines"]
    AGG --> DB
    EXP --> DB
    DB --> API
    API --> U

    classDef client fill:#dbeafe,stroke:#2563eb,color:#1e3a8a,stroke-width:2px
    classDef app fill:#cffafe,stroke:#0891b2,color:#164e63,stroke-width:2px
    classDef service fill:#f3e8ff,stroke:#9333ea,color:#581c87,stroke-width:2px
    classDef database fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:2px
    class U client
    class API,VAL app
    class EMP,ATT,REG,ANA,EXP,AGG service
    class DB database
```

### Request lifecycle

<p align="center">
  <img src="https://img.shields.io/badge/01-Request-2563EB?style=for-the-badge" alt="Request" />
  <img src="https://img.shields.io/badge/02-Validate-0891B2?style=for-the-badge" alt="Validate" />
  <img src="https://img.shields.io/badge/03-Process-7C3AED?style=for-the-badge" alt="Process" />
  <img src="https://img.shields.io/badge/04-MongoDB-16A34A?style=for-the-badge" alt="MongoDB" />
  <img src="https://img.shields.io/badge/05-Response-EA580C?style=for-the-badge" alt="Response" />
</p>

1. Receive the HTTP request through FastAPI.
2. Validate input using Pydantic v2.
3. Execute the relevant employee, attendance, regularization, or analytics operation.
4. Read or update MongoDB using PyMongo.
5. Return the API response with the appropriate status code.

## 🧰 Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.11+ | Application language |
| FastAPI | REST API framework |
| PyMongo | MongoDB connectivity |
| MongoDB 6.0+ | Document database and aggregation pipelines |
| Pydantic v2 | Request validation and data schemas |
| python-dotenv | Environment-variable loading |
| Uvicorn | ASGI server |
| Pytest | Automated testing |

## 🔌 API Endpoint Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Readiness probe; returns 200 when MongoDB responds, otherwise 503 |
| `POST` | `/employees` | Create employee; 409 for duplicate `emp_code`, 422 for invalid input |
| `GET` | `/employees` | List employees with department filtering, pagination, and sorting |
| `POST` | `/attendance/punch-in` | Record punch-in; race-safe through a unique index |
| `GET` | `/attendance` | List attendance with employee, date, and status filters |
| `POST` | `/attendance/punch-out` | Record punch-out, including workflows spanning midnight |
| `PATCH` | `/attendance/{emp_code}/{date}` | Regularize attendance with an audit trail |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Employee monthly analytics |
| `GET` | `/analytics/departments/summary` | Monthly department summary across collections |
| `GET` | `/analytics/leaderboard/late` | Late-comer leaderboard with competition ranking |
| `GET` | `/analytics/departments/{department}/trend` | Daily attendance trend and 7-day moving average |
| `GET` | `/admin/explain/{endpoint}` | MongoDB explain output for a named endpoint's main query |

## 📊 Analytics Capabilities

<details>
<summary><b>👤 Employee Monthly Summary</b></summary>

Returns a monthly summary for a specified employee using MongoDB aggregation.

</details>

<details>
<summary><b>🏢 Department Summary</b></summary>

Produces monthly department analytics using a cross-collection aggregation. Employees who joined on or before the end of the month are included even when they have no attendance logs.

</details>

<details>
<summary><b>🏆 Late-Comer Leaderboard</b></summary>

Uses competition ranking, such as `1, 2, 2, 4`. The requested limit is applied after ranking, and all employees tied at the cutoff are included.

</details>

<details>
<summary><b>📈 Department Attendance Trend</b></summary>

Returns daily attendance trends with a 7-day moving average. The requested date range is limited to 92 days.

</details>

<details>
<summary><b>🔍 Query Explanation</b></summary>

Exposes MongoDB explain output for the main query associated with a named endpoint.

</details>

## ⚙️ Prerequisites

- Python 3.11 or newer.
- MongoDB 6.0 or newer, either MongoDB Community Server locally or MongoDB Atlas.
- PowerShell on Windows for the commands below.
- Docker is **not required**.

## 🚀 Installation

### 1. Clone the repository

```powershell
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_PROJECT_DIRECTORY>
```

Replace the placeholders with your actual GitHub repository URL and project directory.

### 2. Create and activate a virtual environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 🔐 Environment Configuration

The application reads `MONGO_URI` and `MONGO_DB` from environment variables. A local `.env` file is suitable for development, and actual environment variables take precedence.

Create a `.env` file in the project root:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

**Security:** Never commit `.env` files, database credentials, or production connection strings to GitHub.

### Using MongoDB Atlas

Set the connection string in the current PowerShell session:

```powershell
$env:MONGO_URI = "mongodb+srv://<user>:<password>@<cluster>/"
$env:MONGO_DB = "attendance_db"
```

Replace the placeholders with your actual Atlas credentials and cluster details.

### Starting local MongoDB on Windows

If MongoDB Community Server is installed as a Windows service, run PowerShell as administrator:

```powershell
Start-Service MongoDB
```

If the service does not exist, verify the MongoDB installation or use MongoDB Atlas instead.

## 🌱 Sample-Data Seeding

```powershell
python sample_seed.py
```

Loads sample employee and attendance records from `sample_data/` into the `employees` and `attendance_logs` collections.

The supplied sample dataset does not cover all hidden evaluation cases, such as ranking ties, weekend records, employees without logs, or employees who join mid-month. Passing the sample dataset alone does not guarantee correctness against the full grader dataset.

## ▶️ Start the Application

```powershell
uvicorn app.main:app --port 8000
```

The application is expected to become ready within 20 seconds when MongoDB is available. The readiness endpoint is:

```text
GET http://127.0.0.1:8000/health
```

A successful health check returns HTTP 200 when MongoDB responds. Otherwise, it returns HTTP 503.

All indexes are created idempotently at startup.

## 🧪 Run Automated Tests

```powershell
python -m pytest -v
```

The test suite uses a uniquely named database and requires a reachable MongoDB instance. It does not install or start Docker.

Seed the sample dataset separately when needed:

```powershell
python sample_seed.py
```

## 📏 Important Rules and Limitations

- **Trend date range:** ranges where `to - from > 92` are rejected with HTTP 422.
- **Leaderboard ties:** the limit applies after ranking, so ties at the cutoff are included.
- **Department summary:** employees who joined on or before month-end count even with zero attendance logs.
- **Rounding:** uses Decimal half-up rounding; Python's built-in `round()` is not used.
- **`punched_at` validation:** rejects values representable in seconds (`< 100000000000`), floats, strings, and out-of-range values with HTTP 422.
- **Punch-in concurrency:** a unique index protects the punch-in operation against race conditions.
- **Startup indexing:** index creation is idempotent.

## 📂 Repository Deliverables

| File | Purpose |
|---|---|
| `app/main.py` | Application code |
| `requirements.txt` | Python dependencies |
| `REVIEW.md` | Defects identified and fixes documented |
| `DECISIONS.md` | Five assignment questions answered |
| `README.md` | Project documentation |
| `sample_data/` | Sample input records, if included in the submission |

## ✅ Submission Checklist

- [x] `app/main.py` — application code in one file
- [x] `requirements.txt` — dependencies at repository root
- [x] `REVIEW.md` — defects found and fixes documented
- [x] `DECISIONS.md` — five questions answered
- [x] `README.md` — project overview, technology stack, setup, and endpoints
- [ ] No `.env` files or secrets committed
- [ ] No Dockerfile or committed data dumps
- [ ] Repository is public and link submitted before the deadline

## 🎯 Project Focus

**Reliable attendance data. Auditable corrections. Meaningful analytics.**

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=rect&color=0:0F172A,50:1D4ED8,100:06B6D4&height=90&section=footer&text=Built%20with%20FastAPI%20%2B%20MongoDB&fontColor=FFFFFF&fontSize=20&animation=fadeIn" width="100%" alt="FastAPI and MongoDB project footer" />
</p>
