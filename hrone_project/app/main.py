"""
Employee Attendance & Analytics API  (HROne Software Engineer Trainee assignment)

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience; real environment
      variables always win). MongoDB 6.0+ required.

The whole implementation lives in this one file, as required by the assignment.
The HTTP contract (paths, parameters, status codes, response fields) and the business
rules R1-R10 are defined in openapi.yaml; the stored data model is in DATA_MODEL.md.
"""
import base64
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Any, Literal, Optional

from bson.binary import Binary
from bson.decimal128 import Decimal128
from bson.objectid import ObjectId
from bson.timestamp import Timestamp
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

# --------------------------------------------------------------------------- #
# Configuration and connection
# --------------------------------------------------------------------------- #
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))          # R1: IST, UTC+05:30
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Epoch-millisecond bounds from the OpenAPI contract (rejects seconds, floats, strings).
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000

PRESENCE_STATUSES = ("PRESENT", "WFH", "ON_DUTY")       # R6
ALL_STATUSES = ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY")
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]


# --------------------------------------------------------------------------- #
# Indexes (created idempotently at startup)
# --------------------------------------------------------------------------- #
def ensure_indexes() -> None:
    """Create every index the queries need, idempotently.

    employees:
      emp_code unique                     - employee identity, duplicate-create guard
      (department, joined_on)             - department filter + R9 headcount scans
      joined_on                           - R9 headcount range scans (department summary)
    attendance_logs:
      (emp_code, date) unique             - one record per employee/day; punch-in race guard
      (date, emp_code)                    - attendance list sort/date range, month-range scans
      (emp_code, punch_in)                - punch-out record selection (most recent punch_in)
      (status, date, emp_code)            - status-filtered listing with the required sort
    """
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="emp_code_1")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_1_joined_on_1")
    db.employees.create_index([("joined_on", ASCENDING)], name="joined_on_1")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="emp_code_1_date_1")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="date_-1_emp_code_1")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="emp_code_1_punch_in_-1")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", DESCENDING), ("emp_code", ASCENDING)], name="status_1_date_-1_emp_code_1")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_indexes()
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Instant helpers: epoch milliseconds <-> BSON datetimes (R1: truncate to seconds)
# --------------------------------------------------------------------------- #
def ms_to_dt(ms: int) -> datetime:
    """Epoch milliseconds -> timezone-aware UTC datetime (exact for integer ms)."""
    return EPOCH + timedelta(milliseconds=ms)


def dt_to_ms(dt: datetime) -> int:
    """Timezone-aware datetime -> integer epoch milliseconds."""
    delta = dt - EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def now_ms() -> int:
    return dt_to_ms(datetime.now(UTC).replace(microsecond=0))


def hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


# --------------------------------------------------------------------------- #
# Business rules R1-R5 (used by punch-in, punch-out and regularization)
# --------------------------------------------------------------------------- #
def attendance_date(punch_in: datetime, employee: dict) -> str:
    """R1: the attendance day is the IST calendar date of the punch-in instant.

    Overnight shifts (shift_end <= shift_start): a punch-in whose IST time is
    earlier than shift_end belongs to the shift that started the previous day.
    """
    ist = punch_in.astimezone(IST)
    if hhmm(employee["shift_end"]) <= hhmm(employee["shift_start"]):
        if ist.time() < hhmm(employee["shift_end"]):
            return (ist.date() - timedelta(days=1)).isoformat()
    return ist.date().isoformat()


def compute_late_minutes(punch_in: datetime, employee: dict, att_date: str) -> int:
    """R2: grace period 10 minutes; late only when strictly more than 10:00 after
    shift_start; then whole minutes (floor) since shift_start, else 0."""
    ist = punch_in.astimezone(IST)
    start = datetime.combine(date.fromisoformat(att_date), hhmm(employee["shift_start"]), tzinfo=IST)
    elapsed = (ist - start).total_seconds()
    if elapsed > 600:
        return int(elapsed // 60)
    return 0


def compute_overtime_minutes(punch_out: datetime, employee: dict, att_date: str) -> int:
    """R3: whole minutes (floor) from shift_end to punch_out, counted only when >= 30.
    Overnight shifts end on the next calendar day."""
    shift_end = hhmm(employee["shift_end"])
    end_day = date.fromisoformat(att_date)
    if shift_end <= hhmm(employee["shift_start"]):
        end_day += timedelta(days=1)
    end = datetime.combine(end_day, shift_end, tzinfo=IST)
    elapsed = (punch_out.astimezone(IST) - end).total_seconds()
    if elapsed >= 1800:
        return int(elapsed // 60)
    return 0


def round_half_up_2(value) -> float:
    """R4/R8: round to 2 decimals, half-up (never banker's rounding)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_half_up_4(value) -> float:
    """R8: round to 4 decimals, half-up (rates)."""
    return float(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: elapsed seconds / 3600, rounded to 2 decimals half-up."""
    seconds = int((punch_out - punch_in).total_seconds())
    return round_half_up_2(Decimal(seconds) / Decimal(3600))


def is_half_day(work_hours: Optional[float]) -> bool:
    """R5: true when the rounded work_hours is strictly less than 4.50."""
    return work_hours is not None and Decimal(str(work_hours)) < Decimal("4.50")


def derived_fields(employee: dict, att_date: str, punch_in: Optional[datetime],
                   punch_out: Optional[datetime]) -> dict:
    """Recompute every derived field from the final punch times (R2-R5)."""
    if punch_in is None:
        return {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    late = compute_late_minutes(punch_in, employee, att_date)
    if punch_out is None:
        return {"late_minutes": late, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    work = compute_work_hours(punch_in, punch_out)
    return {
        "late_minutes": late,
        "work_hours": work,
        "overtime_minutes": compute_overtime_minutes(punch_out, employee, att_date),
        "half_day": is_half_day(work),
    }


# --------------------------------------------------------------------------- #
# Calendar helpers for analytics (R7 working days, month bounds)
# --------------------------------------------------------------------------- #
def month_bounds(month: str) -> tuple[str, str]:
    """'YYYY-MM' -> ('YYYY-MM-01', last day of the month)."""
    year, number = month.split("-")
    try:
        date(int(year), int(number), 1)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid month: {month}")
    last = [31, 29 if int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][int(number) - 1]
    return f"{month}-01", f"{month}-{last:02d}"


def working_days_between(start: date, end: date) -> int:
    """R7: Monday-Friday, no holiday calendar."""
    count, current = 0, start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def parse_calendar_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"invalid date: {value}")


# --------------------------------------------------------------------------- #
# Serialization: MongoDB documents -> API responses (never expose _id)
# --------------------------------------------------------------------------- #
def _value_to_api(value):
    if isinstance(value, datetime):
        return dt_to_ms(value)
    return value


def serialize_history_entry(entry: dict) -> dict:
    return {
        "at": dt_to_ms(entry["at"]) if isinstance(entry.get("at"), datetime) else entry.get("at"),
        "by": entry.get("by"),
        "reason": entry.get("reason"),
        "changes": {
            field: {"from": _value_to_api(change.get("from")), "to": _value_to_api(change.get("to"))}
            for field, change in entry.get("changes", {}).items()
        },
    }


def serialize_attendance(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": _value_to_api(doc.get("punch_in")),
        "punch_out": _value_to_api(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": [serialize_history_entry(h) for h in doc.get("history", [])],
    }


def serialize_employee(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": dt_to_ms(doc["created_at"]) if isinstance(doc.get("created_at"), datetime) else doc.get("created_at"),
    }


# --------------------------------------------------------------------------- #
# Request models (Pydantic v2; strict ints reject seconds/floats/strings)
# --------------------------------------------------------------------------- #
EpochMillis = Annotated[StrictInt, Field(ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)]
DateString = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
MonthString = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: DateString

    @field_validator("joined_on")
    @classmethod
    def _joined_on_is_real_date(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on is not a valid calendar date")
        return value

    @model_validator(mode="after")
    def _shifts_must_differ(self) -> "EmployeeIn":
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[EpochMillis] = None


class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[EpochMillis] = None
    punch_out: Optional[EpochMillis] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _status_cannot_be_null(self) -> "RegularizeIn":
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class EmployeeResponse(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str
    shift_end: str
    joined_on: str
    created_at: int


class EmployeePageResponse(BaseModel):
    items: list[EmployeeResponse]
    total: int
    page: int
    page_size: int


class HistoryEntryResponse(BaseModel):
    at: int
    by: str
    reason: str
    changes: dict[str, dict[str, Any]]


class AttendanceResponse(BaseModel):
    emp_code: str
    date: str
    status: Status
    punch_in: Optional[int]
    punch_out: Optional[int]
    work_hours: Optional[float]
    late_minutes: int
    overtime_minutes: int
    half_day: bool
    history: list[HistoryEntryResponse]


class AttendancePageResponse(BaseModel):
    items: list[AttendanceResponse]
    total: int
    page: int
    page_size: int


class EmployeeMonthlyResponse(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float]


class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float]
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int


class DepartmentSummaryResponse(BaseModel):
    month: str
    items: list[DepartmentSummaryItem]


class LeaderboardItem(BaseModel):
    rank: int
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int


class LeaderboardResponse(BaseModel):
    month: str
    items: list[LeaderboardItem]


class TrendItem(BaseModel):
    date: str
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float]
    moving_avg_7d: Optional[float]


class TrendResponse(BaseModel):
    department: str
    items: list[TrendItem]


class ExplainResponse(BaseModel):
    endpoint: str
    collection: str
    explain: dict[str, Any]


def json_compatible(value: Any) -> Any:
    """Convert explain-result BSON values while preserving their useful data.

    BSON timestamps use ``{"time": ..., "inc": ...}``; Decimal128 and binary
    values use Extended-JSON-style objects so their precision/subtype is retained.
    """
    if isinstance(value, Timestamp):
        return {"time": int(value.time), "inc": int(value.inc)}
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return {"$numberDecimal": str(value)}
    if isinstance(value, Binary):
        return {
            "$binary": {
                "base64": base64.b64encode(value).decode("ascii"),
                "subType": f"{value.subtype:02x}",
            },
        }
    if isinstance(value, bytes):
        return {
            "$binary": {
                "base64": base64.b64encode(value).decode("ascii"),
                "subType": "00",
            },
        }
    if isinstance(value, dict):
        return {key: json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    return value


# --------------------------------------------------------------------------- #
# Aggregation pipelines (shared by the analytics endpoints and /admin/explain)
# --------------------------------------------------------------------------- #
# Weekday of an attendance date string: parse the calendar label as a UTC midnight
# date; the weekday of "YYYY-MM-DD" is timezone independent. A missing/invalid date
# falls back to 1970-01-01 (a Thursday) so the expression never errors; the
# surrounding conditions already guarantee the row carries a real presence record.
def _date_expr(field: str) -> dict:
    return {"$dateFromString": {"dateString": {"$ifNull": [field, "1970-01-01"]}}}


def _weekday_expr(field: str) -> dict:
    return {"$dayOfWeek": {"date": _date_expr(field)}}


def _is_weekday(field: str) -> dict:
    dow = _weekday_expr(field)
    return {"$and": [{"$gte": [dow, 2]}, {"$lte": [dow, 6]}]}          # R7: Mon-Fri


def _round_half_up_4_expr(field: str) -> dict:
    """Exact 4-decimal half-up rounding in decimal128 arithmetic (R8).

    MongoDB's $round uses banker's rounding, so the aggregation instead computes
    floor(value * 10^4 + 0.5) / 10^4 in decimal128, which is exact half-up and
    matches a Decimal-based reference implementation.
    """
    value = "$" + field
    return {"$cond": [
        {"$ne": [value, None]},
        {"$divide": [
            {"$floor": {"$add": [
                {"$multiply": [{"$toDecimal": value}, Decimal128("10000")]},
                Decimal128("0.5"),
            ]}},
            Decimal128("10000"),
        ]},
        None,
    ]}


def employee_monthly_pipeline(emp_code: str, month_start: str, month_end: str) -> list:
    """Monthly summary for one employee; reads the stored derived fields only."""
    presence = {"$in": ["$status", list(PRESENCE_STATUSES)]}                  # R6
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": month_start, "$lte": month_end}}},
        {"$group": {"_id": None,
                    "total_records": {"$sum": 1},
                    "present_days": {"$sum": {"$cond": [
                        {"$and": [presence, _is_weekday("$date")]},          # R7: Mon-Fri only
                        {"$cond": ["$half_day", 0.5, 1]},                    # R5: half day = 0.5
                        0]}},
                    "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                    "total_late_minutes": {"$sum": {"$cond": [
                        {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                        {"$ifNull": ["$late_minutes", 0]}, 0]}},
                    "total_overtime_minutes": {"$sum": {"$cond": [
                        {"$gt": [{"$ifNull": ["$overtime_minutes", 0]}, 0]},
                        {"$ifNull": ["$overtime_minutes", 0]}, 0]}},
                    }},
    ]


def department_summary_pipeline(month_start: str, month_end: str, department: Optional[str]) -> list:
    """Department summary for a month, across employees and attendance_logs.

    Driven from `employees` so that R9 headcount includes eligible employees with
    zero logs and excludes employees who joined after the month. Each employee is
    joined with all of its logs (equality $lookup, index backed); rows for logs
    outside the month contribute nothing but keep the employee in the headcount.
    """
    match = {"joined_on": {"$lte": month_end}}                                # R9
    if department is not None:
        match["department"] = department
    presence = {"$in": ["$logs.status", list(PRESENCE_STATUSES)]}
    in_month = {"$and": [{"$gte": ["$logs.date", month_start]}, {"$lte": ["$logs.date", month_end]}]}
    present_weekday = {"$and": [presence, in_month, _is_weekday("$logs.date")]}
    has_work_hours = {"$and": [presence, in_month, {"$ne": ["$logs.work_hours", None]}]}
    is_late = {"$and": [in_month, {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}]}
    return [
        {"$match": match},
        {"$lookup": {"from": "attendance_logs", "localField": "emp_code",
                     "foreignField": "emp_code", "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department",
                    "headcount_set": {"$addToSet": "$emp_code"},
                    "present_days": {"$sum": {"$cond": [present_weekday,
                                                        {"$cond": ["$logs.half_day", 0.5, 1]}, 0]}},
                    "work_hours_avg": {"$avg": {"$cond": [has_work_hours, "$logs.work_hours", None]}},
                    "late_count": {"$sum": {"$cond": [is_late, 1, 0]}},
                    "total_late_minutes": {"$sum": {"$cond": [is_late, {"$ifNull": ["$logs.late_minutes", 0]}, 0]}},
                    "leave_count": {"$sum": {"$cond": [
                        {"$and": [in_month, {"$eq": ["$logs.status", "LEAVE"]}]}, 1, 0]}},
                    "on_duty_count": {"$sum": {"$cond": [
                        {"$and": [in_month, {"$eq": ["$logs.status", "ON_DUTY"]}]}, 1, 0]}},
                    }},
        {"$project": {"_id": 0, "department": "$_id",
                      "headcount": {"$size": "$headcount_set"},
                      "present_days": 1, "work_hours_avg": 1,
                      "late_count": 1, "total_late_minutes": 1,
                      "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": ASCENDING}},
        {"$sort": {"department": ASCENDING}},
    ]


def late_leaderboard_pipeline(month_start: str, month_end: str, limit: int,
                              department: Optional[str]) -> list:
    """Late-comers leaderboard: competition ranking (1, 2, 2, 4) via $rank.

    Ranking happens before the limit is applied, so tied employees at the cutoff
    are all returned. Order: total_late_minutes desc, then emp_code asc.
    """
    pipeline = [
        {"$match": {"date": {"$gte": month_start, "$lte": month_end}}},
        {"$group": {"_id": "$emp_code",
                    "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                    "late_count": {"$sum": {"$cond": [
                        {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id",
                     "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"},                                                  # unknown employees are ignored
    ]
    if department is not None:
        pipeline.append({"$match": {"emp.department": department}})            # rank within the department
    pipeline += [
        {"$setWindowFields": {"sortBy": {"total_late_minutes": DESCENDING},
                              "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},                                 # limit applied AFTER ranking
        {"$sort": {"total_late_minutes": DESCENDING, "_id": ASCENDING}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id",
                      "name": "$emp.name", "department": "$emp.department",
                      "total_late_minutes": 1, "late_count": 1}},
    ]
    return pipeline


def department_trend_pipeline(department: str, from_date: date, to_date: date) -> list:
    """Build a calendar-complete, department-specific daily trend in MongoDB."""
    first = datetime(from_date.year, from_date.month, from_date.day, tzinfo=UTC)
    beyond = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, 999000, tzinfo=UTC)
    pipeline = [
        {"$match": {"date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}}},
        {"$lookup": {"from": "employees", "localField": "emp_code",
                     "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {"$group": {"_id": {"$dateFromString": {"dateString": "$date"}},
                    "present_count": {"$sum": {"$cond": [
                        {"$in": ["$status", list(PRESENCE_STATUSES)]},
                        {"$cond": ["$half_day", 0.5, 1]}, 0]}},
                    "late_count": {"$sum": {"$cond": [
                        {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}},
        {"$unionWith": {
            "coll": "employees",
            "pipeline": [
                {"$match": {"department": department}},
                {"$limit": 1},
                {"$project": {
                    "_id": 0,
                    "date": {"$literal": first},
                    "present_count": {"$literal": 0},
                    "late_count": {"$literal": 0},
                }},
            ],
        }},
        {"$group": {"_id": "$date",
                    "present_count": {"$sum": "$present_count"},
                    "late_count": {"$sum": "$late_count"}}},
        {"$project": {"_id": 0, "date": "$_id", "present_count": 1, "late_count": 1}},
        {"$densify": {"field": "date", "range": {
            "step": 1, "unit": "day", "bounds": [first, beyond],
        }}},
        {"$sort": {"date": ASCENDING}},
        {"$set": {
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
            "date_label": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
            "is_working_day": {"$and": [
                {"$gte": [{"$dayOfWeek": "$date"}, 2]},
                {"$lte": [{"$dayOfWeek": "$date"}, 6]},
            ]},
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"trend_day": "$date_label"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$department", department]},
                    {"$lte": ["$joined_on", "$$trend_day"]},
                ]}}},
                {"$count": "headcount"},
            ],
            "as": "headcount_result",
        }},
        {"$set": {
            "headcount": {"$ifNull": [
                {"$arrayElemAt": ["$headcount_result.headcount", 0]}, 0,
            ]},
        }},
        {"$set": {
            "attendance_rate": {"$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$divide": [
                    {"$toDecimal": "$present_count"},
                    {"$toDecimal": "$headcount"},
                ]},
                None,
            ]},
        }},
        {"$set": {"attendance_rate": _round_half_up_4_expr("attendance_rate")}},
        {"$setWindowFields": {
            "sortBy": {"date": ASCENDING},
            "output": {"moving_avg_7d_raw": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]},
            }},
        }},
        {"$set": {"moving_avg_7d": _round_half_up_4_expr("moving_avg_7d_raw")}},
        {"$project": {
            "_id": 0,
            "date": "$date_label",
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": {"$toDouble": "$attendance_rate"},
            "moving_avg_7d": {"$toDouble": "$moving_avg_7d"},
        }},
        {"$sort": {"date": ASCENDING}},
    ]
    return pipeline


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@app.get("/health", response_model=HealthResponse, responses={503: {"model": ErrorResponse}})
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


# --------------------------------------------------------------------------- #
# Employees
# --------------------------------------------------------------------------- #
@app.post("/employees", status_code=201, response_model=EmployeeResponse,
          responses={409: {"model": ErrorResponse}})
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)                     # unique emp_code index guards the race
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees", response_model=EmployeePageResponse)
def list_employees(
    department: Optional[str] = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    query = {"department": department} if department is not None else {}
    skip = (page - 1) * page_size                        # R10: page starts at 1
    total = db.employees.count_documents(query)          # total reflects the filter
    items = list(db.employees.find(query, {"_id": 0})
                 .sort("emp_code", ASCENDING)
                 .skip(skip)
                 .limit(page_size))
    return {"items": [serialize_employee(doc) for doc in items],
            "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Attendance: punch-in, punch-out, listing
# --------------------------------------------------------------------------- #
@app.post("/attendance/punch-in", status_code=201, response_model=AttendanceResponse,
          responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="unknown employee")

    instant = ms_to_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC)
    instant = instant.replace(microsecond=0)               # R1: truncate to whole seconds
    att_date = attendance_date(instant, employee)          # R1

    record = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status,
        "punch_in": instant,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(instant, employee, att_date),   # R2
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(record)              # unique (emp_code, date) index
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    return serialize_attendance(record)


@app.post("/attendance/punch-out", response_model=AttendanceResponse,
          responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="unknown employee")

    instant = ms_to_dt(body.punched_at) if body.punched_at is not None else datetime.now(UTC)
    instant = instant.replace(microsecond=0)               # R1: truncate to whole seconds

    # Most recent record whose punch_in is at or before the punch-out instant.
    # punch_in must be a real instant: $gt EPOCH also drops null punch_in values,
    # which MongoDB sorts before every date (R1; overnight shifts cross midnight).
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$gt": EPOCH, "$lte": instant}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(status_code=404, detail="no punch-in found for this employee")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="record is already punched out")
    if instant <= record["punch_in"]:
        raise HTTPException(status_code=422, detail="punched_at must be after punch_in")
    if instant > record["punch_in"] + timedelta(hours=24):
        raise HTTPException(status_code=422, detail="punched_at is more than 24 hours after punch_in")

    derived = derived_fields(employee, record["date"], record["punch_in"], instant)
    # Atomic conditional update: the punch_out: None guard means two simultaneous
    # punch-outs cannot both succeed - exactly one gets 200, the other gets 409.
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {"punch_out": instant,
                  "work_hours": derived["work_hours"],
                  "overtime_minutes": derived["overtime_minutes"],
                  "half_day": derived["half_day"]}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="record is already punched out")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance(updated)


@app.get("/attendance", response_model=AttendancePageResponse)
def list_attendance(
    emp_code: Optional[str] = Query(default=None),
    date_from: Optional[DateString] = Query(default=None),
    date_to: Optional[DateString] = Query(default=None),
    status: Optional[Status] = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    if date_from is not None:
        parse_calendar_date(date_from)
    if date_to is not None:
        parse_calendar_date(date_to)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be later than date_to")

    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from or date_to:                               # inclusive range (R10-adjacent, per contract)
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        query["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(query)      # filtered total
    items = list(db.attendance_logs.find(query, {"_id": 0})
                 .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
                 .skip(skip)
                 .limit(page_size))
    return {"items": [serialize_attendance(doc) for doc in items],
            "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# Attendance: manual regularization (audit trail)
# --------------------------------------------------------------------------- #
@app.patch("/attendance/{emp_code}/{date}", response_model=AttendanceResponse,
           responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def regularize_attendance(
    emp_code: str = Path(...),
    date: DateString = Path(...),
    body: RegularizeIn = Body(...),
):
    parse_calendar_date(date)
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="unknown employee")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=404, detail="no attendance record for this date")

    supplied = body.model_fields_set
    old_status = record.get("status")
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")

    new_status = body.status if "status" in supplied else old_status
    new_punch_in = ms_to_dt(body.punch_in).replace(microsecond=0) if body.punch_in is not None else old_punch_in
    new_punch_out = ms_to_dt(body.punch_out).replace(microsecond=0) if body.punch_out is not None else old_punch_out
    if body.punch_in is None and "punch_in" in supplied:
        new_punch_in = None                              # explicit null clears the field
    if body.punch_out is None and "punch_out" in supplied:
        new_punch_out = None

    # Contract rules: ABSENT/LEAVE clear both punch times; supplying punch times
    # with them is 422. A presence status requires a punch_in (supplied or stored).
    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(status_code=422, detail="cannot supply punch times with ABSENT or LEAVE")
        new_punch_in = None
        new_punch_out = None
    elif new_punch_in is None:
        raise HTTPException(status_code=422, detail="a presence status requires a punch_in")

    if new_punch_in is not None:
        if attendance_date(new_punch_in, employee) != date:   # R1: punch_in stays on the record's date
            raise HTTPException(status_code=422, detail="punch_in must fall on the record's attendance date")
        if new_punch_out is not None:
            if new_punch_out <= new_punch_in:
                raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
            if new_punch_out > new_punch_in + timedelta(hours=24):
                raise HTTPException(status_code=422, detail="punch_out must be within 24 hours of punch_in")

    if (new_status == old_status and new_punch_in == old_punch_in and new_punch_out == old_punch_out):
        raise HTTPException(status_code=422, detail="the request changes nothing")

    derived = derived_fields(employee, date, new_punch_in, new_punch_out)
    new_values = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "work_hours": derived["work_hours"],
        "late_minutes": derived["late_minutes"],
        "overtime_minutes": derived["overtime_minutes"],
        "half_day": derived["half_day"],
    }

    # Audit trail: exactly one entry per successful correction, listing only the
    # fields that actually changed (punch times as epoch milliseconds).
    changes = {}
    old_values = {"status": old_status, "punch_in": old_punch_in, "punch_out": old_punch_out,
                  "work_hours": record.get("work_hours"), "late_minutes": record.get("late_minutes", 0),
                  "overtime_minutes": record.get("overtime_minutes", 0),
                  "half_day": record.get("half_day", False)}
    for field, new_value in new_values.items():
        old_value = old_values[field]
        if old_value != new_value:
            changes[field] = {"from": _value_to_api(old_value), "to": _value_to_api(new_value)}

    entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Conditional update on the previously read state: a concurrent correction
    # invalidates the guard, so history entries are never silently overwritten
    # (the loser of the race gets 409 and may retry).
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "status": old_status,
         "punch_in": old_punch_in, "punch_out": old_punch_out},
        {"$set": new_values, "$push": {"history": entry}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="the record was modified concurrently")

    updated = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    return serialize_attendance(updated)


# --------------------------------------------------------------------------- #
# Analytics: employee monthly, department summary, leaderboard, trend
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly", response_model=EmployeeMonthlyResponse,
         responses={404: {"model": ErrorResponse}})
def employee_monthly(
    emp_code: str,
    month: MonthString = Query(...),
):
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="unknown employee")

    month_start, month_end = month_bounds(month)
    result = list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code, month_start, month_end)))
    stats = result[0] if result else {}

    joined_on = date.fromisoformat(employee["joined_on"])
    period_start = max(date.fromisoformat(month_start), joined_on)   # R7: count from joined_on
    working_days = working_days_between(period_start, date.fromisoformat(month_end))
    present_days = round_half_up_2(stats.get("present_days", 0))
    attendance_pct = None
    if working_days > 0:
        attendance_pct = round_half_up_4(Decimal(str(present_days)) / Decimal(working_days) * 100)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary", response_model=DepartmentSummaryResponse)
def department_summary(
    month: MonthString = Query(...),
    department: Optional[str] = Query(default=None),
):
    month_start, month_end = month_bounds(month)
    rows = list(db.employees.aggregate(department_summary_pipeline(month_start, month_end, department)))
    items = []
    for row in rows:
        items.append({
            "department": row["department"],
            "headcount": row["headcount"],
            "present_days": round_half_up_2(row["present_days"]),
            "avg_work_hours": None if row.get("work_hours_avg") is None else round_half_up_2(row["work_hours_avg"]),
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"],
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late", response_model=LeaderboardResponse)
def late_leaderboard(
    month: MonthString = Query(...),
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    department: Optional[str] = Query(default=None),
):
    month_start, month_end = month_bounds(month)
    rows = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month_start, month_end, limit, department)))
    return {"month": month, "items": rows}


@app.get("/analytics/departments/{department}/trend", response_model=TrendResponse,
         responses={404: {"model": ErrorResponse}})
def department_trend(
    department: str,
    from_date: DateString = Query(..., alias="from"),
    to_date: DateString = Query(..., alias="to"),
):
    start = parse_calendar_date(from_date)
    end = parse_calendar_date(to_date)
    if end < start:
        raise HTTPException(status_code=422, detail="to must not be earlier than from")
    if (end - start).days > 92:
        raise HTTPException(status_code=422, detail="the range exceeds 92 days")
    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(status_code=404, detail="unknown department")

    rows = db.attendance_logs.aggregate(department_trend_pipeline(department, start, end))
    fields = (
        "date", "is_working_day", "headcount", "present_count", "late_count",
        "attendance_rate", "moving_avg_7d",
    )
    items = [{field: row[field] for field in fields} for row in rows]
    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Admin: MongoDB explain for an endpoint's main query
# --------------------------------------------------------------------------- #
@app.get("/admin/explain/{endpoint}", response_model=ExplainResponse)
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary",
                        "late_leaderboard", "department_trend"] = Path(...),
                     emp_code: Optional[str] = Query(default=None),
                     month: Optional[MonthString] = Query(default=None),
                     department: Optional[str] = Query(default=None),
                     limit: Annotated[int, Query(ge=1, le=50)] = 10,
                     date_from: Optional[DateString] = Query(default=None),
                     date_to: Optional[DateString] = Query(default=None),
                     status: Optional[Status] = Query(default=None),
                     from_date: Optional[DateString] = Query(default=None, alias="from"),
                     to_date: Optional[DateString] = Query(default=None, alias="to"),
                     page: Annotated[int, Query(ge=1)] = 1,
                     page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    def need(value, name):
        if value is None:
            raise HTTPException(status_code=422, detail=f"missing required parameter: {name}")
        return value

    if endpoint == "attendance_list":
        query = {}
        if emp_code is not None:
            query["emp_code"] = emp_code
        if date_from or date_to:
            if date_from is not None:
                parse_calendar_date(date_from)
            if date_to is not None:
                parse_calendar_date(date_to)
            if date_from is not None and date_to is not None and date_from > date_to:
                raise HTTPException(status_code=422, detail="date_from must not be later than date_to")
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if status:
            if status not in ALL_STATUSES:
                raise HTTPException(status_code=422, detail=f"invalid status: {status}")
            query["status"] = status
        explain = db.command("explain", {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }, verbosity="executionStats")
        return json_compatible({
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        })

    if endpoint == "employee_monthly":
        emp_code = need(emp_code, "emp_code")
        month = need(month, "month")
        month_start, month_end = month_bounds(month)
        explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": employee_monthly_pipeline(emp_code, month_start, month_end),
            "cursor": {},
        }, verbosity="executionStats")
        return json_compatible({
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        })

    if endpoint == "department_summary":
        month = need(month, "month")
        month_start, month_end = month_bounds(month)
        explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": department_summary_pipeline(month_start, month_end, department),
            "cursor": {},
        }, verbosity="executionStats")
        return json_compatible({
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain,
        })

    if endpoint == "late_leaderboard":
        month = need(month, "month")
        month_start, month_end = month_bounds(month)
        explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": late_leaderboard_pipeline(month_start, month_end, limit, department),
            "cursor": {},
        }, verbosity="executionStats")
        return json_compatible({
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        })

    if endpoint == "department_trend":
        department = need(department, "department")
        from_date = need(from_date, "from")
        to_date = need(to_date, "to")
        start = parse_calendar_date(from_date)
        end = parse_calendar_date(to_date)
        if end < start:
            raise HTTPException(status_code=422, detail="to must not be earlier than from")
        if (end - start).days > 92:
            raise HTTPException(status_code=422, detail="the range exceeds 92 days")
        explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": department_trend_pipeline(department, start, end),
            "cursor": {},
        }, verbosity="executionStats")
        return json_compatible({
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        })

    raise HTTPException(status_code=422, detail=f"unknown endpoint: {endpoint}")
