"""Endpoint and business-rule tests for the HROne attendance API."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier

from bson.binary import Binary
from bson.decimal128 import Decimal128
from bson.objectid import ObjectId
from bson.timestamp import Timestamp
from fastapi.testclient import TestClient

from app import main
from app.main import working_days_between


IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def ist_time(day: int, hour: int, minute: int, second: int = 0, millisecond: int = 0) -> int:
    return epoch_ms(datetime(2026, 7, day, hour, minute, second, millisecond * 1000, tzinfo=IST))


def employee_payload(
    emp_code: str,
    department: str = "Quality",
    joined_on: str = "2026-07-01",
    shift_start: str = "09:30",
    shift_end: str = "18:30",
) -> dict:
    return {
        "emp_code": emp_code,
        "name": f"Employee {emp_code}",
        "email": f"{emp_code.lower()}@example.com",
        "department": department,
        "shift_start": shift_start,
        "shift_end": shift_end,
        "joined_on": joined_on,
    }


def add_employee(api, emp_code: str, **kwargs) -> dict:
    response = api.post("/employees", json=employee_payload(emp_code, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def add_log(
    mongo_db,
    emp_code: str,
    day: str,
    *,
    status: str = "PRESENT",
    punch_in: datetime | None = None,
    punch_out: datetime | None = None,
    work_hours: float | None = None,
    late_minutes: int = 0,
    overtime_minutes: int = 0,
    half_day: bool = False,
) -> None:
    mongo_db.attendance_logs.insert_one({
        "emp_code": emp_code,
        "date": day,
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": [],
    })


class TestHealthAndEmployees:
    def test_health_ready(self, test_client):
        response = test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_create_employee_and_list_pagination(self, test_client):
        add_employee(test_client, "EMP9001", department="Engineering Test")
        add_employee(test_client, "EMP9002", department="Engineering Test")
        add_employee(test_client, "EMP9003", department="Sales")

        response = test_client.get("/employees?department=Engineering%20Test&page=2&page_size=1")
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert response.json()["page"] == 2
        assert response.json()["page_size"] == 1
        assert response.json()["items"][0]["emp_code"] == "EMP9002"
        assert "created_at" in response.json()["items"][0]

    def test_employee_duplicate_and_validation(self, test_client):
        payload = employee_payload("EMP9001")
        assert test_client.post("/employees", json=payload).status_code == 201
        assert test_client.post("/employees", json=payload).status_code == 409

        for changed in (
            {"emp_code": "EMP01"},
            {"email": "not-an-email"},
            {"joined_on": "2026-02-30"},
            {"shift_start": "09:30", "shift_end": "09:30"},
            {"shift_start": "25:00"},
        ):
            response = test_client.post("/employees", json={**payload, **changed})
            assert response.status_code == 422

    def test_employee_pagination_bounds(self, test_client):
        assert test_client.get("/employees?page=0").status_code == 422
        assert test_client.get("/employees?page_size=101").status_code == 422

    def test_required_indexes_are_created(self, test_client, mongo_db):
        employee_indexes = mongo_db.employees.index_information()
        attendance_indexes = mongo_db.attendance_logs.index_information()
        assert employee_indexes["emp_code_1"]["unique"] is True
        assert employee_indexes["department_1_joined_on_1"]["key"] == [
            ("department", 1), ("joined_on", 1),
        ]
        assert attendance_indexes["emp_code_1_date_1"]["unique"] is True
        assert "date_-1_emp_code_1" in attendance_indexes
        assert "emp_code_1_punch_in_-1" in attendance_indexes
        assert "status_1_date_-1_emp_code_1" in attendance_indexes

    def test_concurrent_duplicate_employee_creation_has_one_winner(self, test_client):
        payload = employee_payload("EMP9001")
        start = Barrier(2)

        def request():
            start.wait()
            return test_client.post("/employees", json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: request(), range(2)))
        assert sorted(statuses) == [201, 409]


class TestPunchOperations:
    def test_punch_in_truncates_milliseconds_and_applies_grace_boundary(self, test_client):
        add_employee(test_client, "EMP9001")
        on_boundary = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001",
            "punched_at": ist_time(6, 9, 40, millisecond=900),
        })
        assert on_boundary.status_code == 201
        assert on_boundary.json()["late_minutes"] == 0
        assert on_boundary.json()["punch_in"] == ist_time(6, 9, 40)
        assert on_boundary.json()["history"] == []

        add_employee(test_client, "EMP9002")
        after_boundary = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9002",
            "punched_at": ist_time(6, 9, 40, second=1),
        })
        assert after_boundary.status_code == 201
        assert after_boundary.json()["late_minutes"] == 10

    def test_punch_in_rejects_invalid_instants_and_unknown_employees(self, test_client):
        assert test_client.post("/attendance/punch-in", json={"emp_code": "EMP9999"}).status_code == 404
        add_employee(test_client, "EMP9001")
        for value in (1_783_312_500, 1_783_312_500.0, "1783312500000", 4_102_444_800_001):
            response = test_client.post("/attendance/punch-in", json={
                "emp_code": "EMP9001", "punched_at": value,
            })
            assert response.status_code == 422

    def test_punch_in_duplicate_is_database_enforced(self, test_client):
        add_employee(test_client, "EMP9001")
        payload = {"emp_code": "EMP9001", "punched_at": ist_time(6, 9, 30)}
        assert test_client.post("/attendance/punch-in", json=payload).status_code == 201
        assert test_client.post("/attendance/punch-in", json=payload).status_code == 409

    def test_punch_in_concurrent_requests_have_one_winner(self, test_client):
        add_employee(test_client, "EMP9001")
        start = Barrier(2)

        def request():
            start.wait()
            return test_client.post("/attendance/punch-in", json={
                "emp_code": "EMP9001", "punched_at": ist_time(6, 9, 30),
            }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: request(), range(2)))
        assert sorted(statuses) == [201, 409]

    def test_punch_out_overtime_half_day_and_time_bounds(self, test_client):
        add_employee(test_client, "EMP9001")
        start = ist_time(6, 9, 30)
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": start,
        }).status_code == 201

        # 4:29:42 rounds half-up to 4.50 hours, so this is not a half day.
        response = test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 13, 59, second=42),
        })
        assert response.status_code == 200
        assert response.json()["work_hours"] == 4.5
        assert response.json()["half_day"] is False

        add_employee(test_client, "EMP9002")
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9002", "punched_at": start,
        }).status_code == 201
        assert test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9002", "punched_at": ist_time(6, 18, 59, second=59),
        }).json()["overtime_minutes"] == 0

        add_employee(test_client, "EMP9003")
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9003", "punched_at": start,
        }).status_code == 201
        assert test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9003", "punched_at": ist_time(6, 19, 0),
        }).json()["overtime_minutes"] == 30

        add_employee(test_client, "EMP9004")
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9004", "punched_at": start,
        }).status_code == 201
        assert test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9004", "punched_at": ist_time(7, 9, 30, second=1),
        }).status_code == 422

    def test_overnight_attendance_and_punch_out(self, test_client):
        add_employee(test_client, "EMP9001", shift_start="22:00", shift_end="06:00")
        early = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(7, 4, 0),
        })
        assert early.status_code == 201
        assert early.json()["date"] == "2026-07-06"

        overnight = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(7, 22, 0),
        })
        assert overnight.status_code == 201
        closed = test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9001", "punched_at": ist_time(8, 6, 30),
        })
        assert closed.status_code == 200
        assert closed.json()["date"] == "2026-07-07"
        assert closed.json()["overtime_minutes"] == 30

    def test_punch_out_no_record_and_concurrent_closing(self, test_client):
        add_employee(test_client, "EMP9001")
        assert test_client.post("/attendance/punch-out", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 18, 30),
        }).status_code == 404
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 9, 30),
        }).status_code == 201

        start = Barrier(2)

        def request():
            start.wait()
            return test_client.post("/attendance/punch-out", json={
                "emp_code": "EMP9001", "punched_at": ist_time(6, 18, 30),
            }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: request(), range(2)))
        assert sorted(statuses) == [200, 409]


class TestAttendanceAndRegularization:
    def test_attendance_listing_filters_and_pagination(self, test_client):
        response = test_client.get("/attendance?emp_code=EMP0001&page=1&page_size=1")
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert len(response.json()["items"]) == 1
        assert response.json()["items"][0]["date"] == "2026-07-07"
        assert test_client.get("/attendance?date_from=2026-07-08&date_to=2026-07-06").status_code == 422
        assert test_client.get("/attendance?date_from=2026-02-30").status_code == 422
        assert test_client.get("/attendance?status=UNKNOWN").status_code == 422

    def test_regularization_recomputes_and_appends_audit_history(self, test_client):
        add_employee(test_client, "EMP9001")
        created = test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 9, 45),
        }).json()
        original_punch = created["punch_in"]

        corrected = test_client.patch("/attendance/EMP9001/2026-07-06", json={
            "punch_in": ist_time(6, 9, 30, millisecond=900),
            "reason": "Corrected badge time",
            "regularized_by": "hr.admin",
        })
        assert corrected.status_code == 200
        result = corrected.json()
        assert result["punch_in"] == ist_time(6, 9, 30)
        assert result["late_minutes"] == 0
        assert len(result["history"]) == 1
        assert result["history"][0]["by"] == "hr.admin"
        assert result["history"][0]["changes"] == {
            "punch_in": {"from": original_punch, "to": ist_time(6, 9, 30)},
            "late_minutes": {"from": 15, "to": 0},
        }

        second = test_client.patch("/attendance/EMP9001/2026-07-06", json={
            "status": "WFH",
            "reason": "Work location updated",
            "regularized_by": "hr.admin",
        })
        assert second.status_code == 200
        assert len(second.json()["history"]) == 2
        assert second.json()["history"][0] == result["history"][0]
        assert second.json()["history"][1]["changes"] == {
            "status": {"from": "PRESENT", "to": "WFH"},
        }

    def test_regularization_validation_and_missing_record(self, test_client):
        assert test_client.patch("/attendance/EMP9999/2026-07-06", json={
            "reason": "Missing record", "regularized_by": "admin",
        }).status_code == 404
        add_employee(test_client, "EMP9001")
        assert test_client.patch("/attendance/EMP9001/2026-07-06", json={
            "reason": "Missing record", "regularized_by": "admin",
        }).status_code == 404
        test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 9, 30),
        })
        url = "/attendance/EMP9001/2026-07-06"
        valid = {"reason": "Correct record", "regularized_by": "admin"}
        assert test_client.patch(url, json=valid).status_code == 422
        assert test_client.patch(url, json={**valid, "status": None}).status_code == 422
        assert test_client.patch(url, json={**valid, "status": "LEAVE", "punch_in": None}).status_code == 422
        assert test_client.patch(url, json={**valid, "status": "PRESENT", "punch_out": ist_time(6, 9, 29)}).status_code == 422

    def test_concurrent_regularizations_never_overwrite_history(self, test_client):
        add_employee(test_client, "EMP9001")
        assert test_client.post("/attendance/punch-in", json={
            "emp_code": "EMP9001", "punched_at": ist_time(6, 9, 30),
        }).status_code == 201
        start = Barrier(2)

        def request(status: str):
            start.wait()
            return test_client.patch("/attendance/EMP9001/2026-07-06", json={
                "status": status,
                "reason": "Concurrent review",
                "regularized_by": status,
            }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(request, ("WFH", "ON_DUTY")))
        assert sorted(statuses) in ([200, 200], [200, 409])
        record = test_client.get("/attendance?emp_code=EMP9001").json()["items"][0]
        assert len(record["history"]) == statuses.count(200)


class TestAnalytics:
    def test_employee_monthly_weekends_half_days_and_join_date(self, test_client, mongo_db):
        add_employee(test_client, "EMP9001", joined_on="2026-07-07")
        add_log(
            mongo_db, "EMP9001", "2026-07-07", punch_in=datetime(2026, 7, 7, 4, tzinfo=UTC),
            punch_out=datetime(2026, 7, 7, 8, 30, tzinfo=UTC), work_hours=4.5,
            half_day=True, late_minutes=15, overtime_minutes=40,
        )
        add_log(
            mongo_db, "EMP9001", "2026-07-04", punch_in=datetime(2026, 7, 4, 4, tzinfo=UTC),
            punch_out=datetime(2026, 7, 4, 13, tzinfo=UTC), work_hours=9,
            late_minutes=10, overtime_minutes=30,
        )
        add_log(mongo_db, "EMP9001", "2026-07-09", status="LEAVE")

        response = test_client.get("/analytics/employees/EMP9001/monthly?month=2026-07")
        assert response.status_code == 200
        body = response.json()
        assert body["working_days"] == working_days_between(date(2026, 7, 7), date(2026, 7, 31))
        assert body["present_days"] == 0.5
        assert body["leave_days"] == 1
        assert body["late_count"] == 2
        assert body["total_late_minutes"] == 25
        assert body["total_overtime_minutes"] == 70
        assert test_client.get("/analytics/employees/EMP9999/monthly?month=2026-07").status_code == 404
        assert test_client.get("/analytics/employees/EMP9001/monthly?month=2026-13").status_code == 422

    def test_department_summary_headcount_and_record_based_average(self, test_client, mongo_db):
        add_employee(test_client, "EMP9001", department="Ops", joined_on="2026-07-01")
        add_employee(test_client, "EMP9002", department="Ops", joined_on="2026-07-20")
        add_employee(test_client, "EMP9003", department="Ops", joined_on="2026-08-01")
        add_log(
            mongo_db, "EMP9001", "2026-07-06", punch_in=datetime(2026, 7, 6, 4, tzinfo=UTC),
            punch_out=datetime(2026, 7, 6, 8, 30, tzinfo=UTC), work_hours=4.5,
            half_day=True, late_minutes=10,
        )
        add_log(mongo_db, "EMP9001", "2026-07-07", status="LEAVE")
        add_log(
            mongo_db, "EMP9002", "2026-07-21", status="ON_DUTY",
            punch_in=datetime(2026, 7, 21, 4, tzinfo=UTC),
        )

        response = test_client.get("/analytics/departments/summary?month=2026-07")
        assert response.status_code == 200
        ops = next(item for item in response.json()["items"] if item["department"] == "Ops")
        assert ops == {
            "department": "Ops", "headcount": 2, "present_days": 1.5,
            "avg_work_hours": 4.5, "late_count": 1, "total_late_minutes": 10,
            "leave_count": 1, "on_duty_count": 1,
        }
        assert test_client.get("/analytics/departments/summary?month=2026-07&department=Ops").json()["items"] == [ops]

    def test_late_leaderboard_ranks_ties_past_cutoff(self, test_client, mongo_db):
        for code in ("EMP9001", "EMP9002", "EMP9003"):
            add_employee(test_client, code, department="Late Team")
        add_log(mongo_db, "EMP9001", "2026-07-06", late_minutes=60)
        add_log(mongo_db, "EMP9002", "2026-07-06", late_minutes=30)
        add_log(mongo_db, "EMP9003", "2026-07-06", late_minutes=30)

        response = test_client.get(
            "/analytics/leaderboard/late?month=2026-07&limit=2&department=Late%20Team"
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [(item["emp_code"], item["rank"]) for item in items] == [
            ("EMP9001", 1), ("EMP9002", 2), ("EMP9003", 2),
        ]

    def test_department_trend_dates_headcount_rates_and_moving_average(self, test_client, mongo_db):
        add_employee(test_client, "EMP9001", department="Trend", joined_on="2026-07-06")
        add_employee(test_client, "EMP9002", department="Trend", joined_on="2026-07-09")
        add_log(
            mongo_db, "EMP9001", "2026-07-06", punch_in=datetime(2026, 7, 6, 4, tzinfo=UTC),
            status="PRESENT", half_day=True, late_minutes=12,
        )
        add_log(
            mongo_db, "EMP9001", "2026-07-08", punch_in=datetime(2026, 7, 8, 4, tzinfo=UTC),
            status="PRESENT",
        )
        response = test_client.get(
            "/analytics/departments/Trend/trend?from=2026-07-06&to=2026-07-12"
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 7
        assert [item["date"] for item in items] == [
            f"2026-07-{day:02d}" for day in range(6, 13)
        ]
        assert [item["headcount"] for item in items] == [1, 1, 1, 2, 2, 2, 2]
        assert [item["present_count"] for item in items] == [0.5, 0, 1, 0, 0, 0, 0]
        assert [item["attendance_rate"] for item in items] == [0.5, 0.0, 1.0, 0.0, 0.0, None, None]
        assert [item["moving_avg_7d"] for item in items] == [0.5, 0.25, 0.5, 0.375, 0.3, 0.3, 0.3]
        assert items[5]["is_working_day"] is False
        assert test_client.get(
            "/analytics/departments/Unknown/trend?from=2026-07-06&to=2026-07-12"
        ).status_code == 404
        assert test_client.get(
            "/analytics/departments/Trend/trend?from=2026-07-12&to=2026-07-06"
        ).status_code == 422
        assert test_client.get(
            "/analytics/departments/Trend/trend?from=2026-01-01&to=2026-04-04"
        ).status_code == 422

    def test_trend_without_attendance_records_still_returns_all_dates(self, test_client):
        add_employee(test_client, "EMP9001", department="Empty Trend", joined_on="2026-07-06")
        response = test_client.get(
            "/analytics/departments/Empty%20Trend/trend?from=2026-07-06&to=2026-07-08"
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 3
        assert [item["attendance_rate"] for item in response.json()["items"]] == [0.0, 0.0, 0.0]

    def test_trend_zero_headcount_keeps_rate_and_average_null(self, test_client):
        add_employee(test_client, "EMP9001", department="Future Team", joined_on="2026-07-07")
        response = test_client.get(
            "/analytics/departments/Future%20Team/trend?from=2026-07-05&to=2026-07-07"
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["headcount"] for item in items] == [0, 0, 1]
        assert [item["attendance_rate"] for item in items] == [None, None, 0.0]
        assert [item["moving_avg_7d"] for item in items] == [None, None, 0.0]

    def test_explain_runs_each_named_query_and_requires_inputs(self, test_client):
        def nested_values(value, key):
            if isinstance(value, dict):
                for name, child in value.items():
                    if name == key:
                        yield child
                    yield from nested_values(child, key)
            elif isinstance(value, list):
                for child in value:
                    yield from nested_values(child, key)

        cases = (
            "/admin/explain/attendance_list?status=PRESENT&page=1&page_size=5",
            "/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07",
            "/admin/explain/department_summary?month=2026-07",
            "/admin/explain/late_leaderboard?month=2026-07&limit=2",
            "/admin/explain/department_trend?department=Engineering&from=2026-07-06&to=2026-07-08",
        )
        for path in cases:
            response = test_client.get(path)
            assert response.status_code == 200, response.text
            result = response.json()
            assert any(nested_values(result["explain"], "queryPlanner"))
            assert any(nested_values(result["explain"], "executionStats"))
        assert test_client.get("/admin/explain/employee_monthly?month=2026-07").status_code == 422
        assert test_client.get("/admin/explain/not_an_endpoint").status_code == 422

    def test_explain_serializes_nested_bson_timestamps(self, monkeypatch):
        def fake_command(*_args, **_kwargs):
            return {
                "queryPlanner": {
                    "winningPlan": {
                        "stage": "IXSCAN",
                        "timestamp": Timestamp(1_783_312_500, 7),
                    },
                },
                "executionStats": {
                    "nReturned": 4,
                    "executionSuccess": True,
                    "details": ({
                        "id": ObjectId("507f1f77bcf86cd799439011"),
                        "observedAt": datetime(2026, 7, 6, tzinfo=UTC),
                        "amount": Decimal128("1.25"),
                    },),
                },
                "$clusterTime": {
                    "clusterTime": Timestamp(1_783_312_501, 8),
                    "signature": {"hash": Binary(b"signature"), "keyId": 9},
                },
            }

        monkeypatch.setattr(main.db, "command", fake_command)
        response = TestClient(main.app).get("/admin/explain/attendance_list")

        assert response.status_code == 200, response.text
        result = response.json()
        assert result["explain"]["queryPlanner"]["winningPlan"]["timestamp"] == {
            "time": 1_783_312_500,
            "inc": 7,
        }
        assert result["explain"]["executionStats"]["nReturned"] == 4
        assert result["explain"]["executionStats"]["executionSuccess"] is True
        assert result["explain"]["executionStats"]["details"] == [{
            "id": "507f1f77bcf86cd799439011",
            "observedAt": "2026-07-06T00:00:00+00:00",
            "amount": {"$numberDecimal": "1.25"},
        }]
        assert result["explain"]["$clusterTime"]["clusterTime"] == {
            "time": 1_783_312_501,
            "inc": 8,
        }
        assert result["explain"]["$clusterTime"]["signature"]["hash"] == {
            "$binary": {"base64": "c2lnbmF0dXJl", "subType": "00"},
        }
