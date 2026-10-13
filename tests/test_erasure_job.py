"""Unit tests for the erasure job in audit_wrapper.py. No Databricks access: SQL is faked.

Run:  python tests/test_erasure_job.py      (from the folder that holds audit_wrapper.py)
"""
import os
import re
import sys
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import audit_wrapper as aw  # noqa: E402

NOW = datetime.now(timezone.utc)


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class FakeWrapper:
    catalog = "shopping_assistant"
    app_id = "myre_app"
    schema_version = "1.0"

    def __init__(self, catalog="shopping_assistant"):
        self.catalog = catalog

    def _tbl(self, name):
        return f"{self.catalog}.{name}"

    def _hmac(self, value):
        return "hmac(" + value + ")"


class FakeSql:
    """Tiny in-memory stand-in for the warehouse, just enough for the job's statements."""

    def __init__(self, requests, traces=("t1", "t2"), pii=2, feedback=0, policy=None,
                 originals=None, break_delete=False, fail_for=None, completed=None, last_vacuum=None):
        self.requests = [list(r) for r in requests]          # [request_id, subject_ref, regulation, requested_at]
        self.traces = list(traces)
        self.pii = {"customer_pii": pii, "feedback_pii": feedback}
        self.policy = policy if policy is not None else [["GDPR", "30", "7"], ["INTERNAL_POLICY", "30", "7"]]
        self.originals = originals if originals is not None else {t: 1 for t in aw.ERASURE_FLAG_TABLES}
        self.flags = {t: 0 for t in aw.ERASURE_FLAG_TABLES}
        self.flagged_traces = set()
        self.break_delete = break_delete
        self.fail_for = fail_for                              # subject_ref whose DELETE raises
        self.completed = completed or []
        self.last_vacuum = last_vacuum
        self.log = []                                         # every statement, in order
        self.inserts = []                                     # (table, row)
        self.rep_requests = []                                # rows inserted into reporting.erasure_requests

    def run(self, sql, params=None):
        params = params or {}
        s = " ".join(sql.split())
        self.log.append((s, dict(params)))
        if "FROM shopping_assistant.reporting.retention_policy" in s:
            return self.policy
        if "FROM shopping_assistant.user_data.erasure_requests" in s and s.startswith("SELECT"):
            return self.requests
        if s.startswith("DELETE FROM"):
            if self.fail_for and params.get("sr") == self.fail_for:
                raise RuntimeError("warehouse exploded")
            if not self.break_delete:
                for t in self.pii:
                    if f".{t} " in s:
                        self.pii[t] = 0
            return []
        if s.startswith("INSERT INTO"):
            table = re.match(r"INSERT INTO (\S+)", s).group(1)
            self.inserts.append((table, dict(params)))
            if table.startswith("shopping_assistant.raw_logs."):
                short = table.split(".")[-1]
                if short in self.flags:
                    self.flags[short] += 1
                    if short == "ai_interactions_raw":
                        self.flagged_traces.add(params["trace_id"])
            if table.endswith("reporting.erasure_requests"):
                self.rep_requests.append(dict(params))
            return []
        if s.startswith("UPDATE") or s.startswith("VACUUM"):
            return []
        if "SELECT count(*) FROM shopping_assistant.raw_logs.customer_pii" in s:
            return [[self.pii["customer_pii"]]]
        if "SELECT count(*) FROM shopping_assistant.raw_logs.feedback_pii" in s:
            return [[self.pii["feedback_pii"]]]
        if "SELECT DISTINCT trace_id" in s and "is_erasure_flag = true" in s:
            return [[t] for t in sorted(self.flagged_traces)]
        if "SELECT DISTINCT trace_id" in s:
            return [[t] for t in self.traces]
        m = re.search(r"SELECT count\(\*\) FROM shopping_assistant\.raw_logs\.(\w+) WHERE", s)
        if m:
            t = m.group(1)
            return [[self.flags[t] if "is_erasure_flag = true" in s else self.originals.get(t, 0)]]
        if "SELECT count(*) FROM shopping_assistant.reporting.erasure_requests" in s:
            return [[len([r for r in self.rep_requests if r["request_id"] == params["rid"]])]]
        if "FROM shopping_assistant.reporting.erasure_requests r" in s:
            return self.completed
        if s.startswith("SELECT") and "reporting.retention_enforcement_log" in s:
            return [[self.last_vacuum]]
        raise AssertionError(f"unexpected SQL: {s}")

    def statements(self, prefix):
        return [(s, p) for s, p in self.log if s.startswith(prefix)]


def make(sql, execute=True, max_requests=20, catalog="shopping_assistant"):
    out = []
    proc = aw.ErasureProcessor(FakeWrapper(catalog), sql=sql, execute=execute,
                               max_requests=max_requests, out=out.append)
    return proc, out


def req(rid="r1", sref="subj1", reg="GDPR", age_days=1):
    return [rid, sref, reg, ts(NOW - timedelta(days=age_days))]


# ── tests ─────────────────────────────────────────────────────────────────

def test_dry_run_writes_nothing():
    sql = FakeSql([req()])
    proc, out = make(sql, execute=False)
    res = proc.process_pending()
    assert res["planned"] == 1 and res["completed"] == 0
    for kw in ("INSERT", "DELETE", "UPDATE", "VACUUM"):
        assert not sql.statements(kw), f"dry run issued {kw}"
    assert sql.pii["customer_pii"] == 2


def test_execute_full_flow():
    sql = FakeSql([req()], traces=("t1", "t2"), pii=2, feedback=1)
    proc, _ = make(sql)
    res = proc.process_pending()
    assert res == {"found": 1, "completed": 1, "failed": 0, "planned": 0}, res
    assert sql.pii == {"customer_pii": 0, "feedback_pii": 0}
    # one flag row per trace in ai_interactions_raw, one per subject in the other six tables
    assert sql.flags["ai_interactions_raw"] == 2
    assert all(sql.flags[t] == 1 for t in aw.ERASURE_FLAG_TABLES if t != "ai_interactions_raw")
    # flag rows carry subject_ref and the flag
    flag_rows = [p for t, p in sql.inserts if t.startswith("shopping_assistant.raw_logs.")]
    assert all(p["subject_ref"] == "subj1" and p["is_erasure_flag"] == "true" for p in flag_rows)
    # proof record: real counts, computed deadline flag
    proof = [p for t, p in sql.inserts if t.endswith("erasure_audit_records")]
    assert len(proof) == 1
    assert str(proof[0]["pii_rows_deleted"]) == "3" and str(proof[0]["traces_affected"]) == "2"
    assert proof[0]["within_deadline"] == "true"
    assert proof[0]["verification_hash"] == "hmac(t1|t2)"
    # reporting request: pending intake then completed, same deadline
    assert [r["status"] for r in sql.rep_requests] == ["pending", "completed"]
    assert sql.rep_requests[0]["request_deadline"] == sql.rep_requests[1]["request_deadline"]
    # user_data row moved processing -> completed
    states = [p["st"] for s, p in sql.statements("UPDATE")]
    assert states == ["processing", "completed"], states


def test_pii_deleted_before_flags_and_proof_last():
    sql = FakeSql([req()])
    proc, _ = make(sql)
    proc.process_pending()
    kinds = []
    for s, _p in sql.log:
        if s.startswith("DELETE"):
            kinds.append("delete")
        elif s.startswith("INSERT INTO shopping_assistant.raw_logs."):
            kinds.append("flag")
        elif "erasure_audit_records" in s and s.startswith("INSERT"):
            kinds.append("proof")
    assert kinds.index("delete") < kinds.index("flag") < kinds.index("proof")
    assert kinds[-1] == "proof"


def test_never_touches_other_tables():
    sql = FakeSql([req()])
    proc, _ = make(sql)
    proc.process_pending()
    forbidden = ("cart_items", "wishlist_items", "orders", "cart_events_raw", "wishlist_events_raw",
                 "order_events_raw", "sandbox", "otel", "user_profiles")
    for s, _p in sql.log:
        if s.startswith(("DELETE", "INSERT", "UPDATE", "VACUUM")):
            assert not any(f in s for f in forbidden), s
    for s, _p in sql.statements("DELETE"):
        assert "app_id = :app" in s and ("subject_ref = :sr" in s or "reviewer_subject_ref = :sr" in s)
    assert {re.search(r"DELETE FROM (\S+)", s).group(1) for s, _ in sql.statements("DELETE")} == {
        "shopping_assistant.raw_logs.customer_pii", "shopping_assistant.raw_logs.feedback_pii"}


def test_rerun_is_idempotent():
    sql = FakeSql([req()])
    proc, _ = make(sql)
    proc.process_pending()
    first_flags = dict(sql.flags)
    sql.rep_requests_before = len(sql.rep_requests)
    sql.requests = [req()]                      # same request shows up again (e.g. status reset)
    proc.process_pending()
    assert sql.flags == first_flags, "flag rows were duplicated"
    pending_rows = [r for r in sql.rep_requests if r["status"] == "pending"]
    assert len(pending_rows) == 1, "intake row was duplicated"


def test_verification_failure_blocks_completion():
    sql = FakeSql([req()], break_delete=True)
    proc, out = make(sql)
    res = proc.process_pending()
    assert res["failed"] == 1 and res["completed"] == 0
    assert not [p for t, p in sql.inserts if t.endswith("erasure_audit_records")], "proof written on failure"
    statuses = [r["status"] for r in sql.rep_requests]
    assert "completed" not in statuses and statuses[-1] == "failed"
    assert "still has" in sql.rep_requests[-1]["blocked_reason"]
    assert sql.statements("UPDATE")[-1][1]["st"] == "failed"


def test_one_failure_does_not_stop_the_rest():
    sql = FakeSql([req("r1", "bad", age_days=5), req("r2", "good", age_days=1)], fail_for="bad")
    proc, _ = make(sql)
    res = proc.process_pending()
    assert res["failed"] == 1 and res["completed"] == 1
    done = [r["request_id"] for r in sql.rep_requests if r["status"] == "completed"]
    assert done == ["r2"]


def test_nearest_deadline_first_and_cap():
    sql = FakeSql([req("new", "s_new", age_days=1), req("old", "s_old", age_days=20), req("mid", "s_mid", age_days=10)])
    proc, _ = make(sql, max_requests=2)
    res = proc.process_pending()
    assert res["found"] == 3 and res["completed"] == 2
    order = [r["request_id"] for r in sql.rep_requests if r["status"] == "pending"]
    assert order == ["old", "mid"], order


def test_deadline_uses_policy_and_fallback():
    sql = FakeSql([req("r1", "s1", reg="UNKNOWN", age_days=1), req("r2", "s2", reg="CCPA", age_days=1)],
                  policy=[["CCPA", "45", "30"], ["INTERNAL_POLICY", "30", "7"]])
    proc, _ = make(sql)
    proc.process_pending()
    rows = {r["request_id"]: r for r in sql.rep_requests if r["status"] == "pending"}
    d1 = datetime.strptime(rows["r1"]["request_deadline"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(rows["r1"]["request_received_at"], "%Y-%m-%d %H:%M:%S")
    d2 = datetime.strptime(rows["r2"]["request_deadline"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(rows["r2"]["request_received_at"], "%Y-%m-%d %H:%M:%S")
    assert d1.days == 30 and d2.days == 45


def test_within_deadline_false_when_late():
    sql = FakeSql([req(age_days=40)])
    proc, _ = make(sql)
    proc.process_pending()
    proof = [p for t, p in sql.inserts if t.endswith("erasure_audit_records")][0]
    assert proof["within_deadline"] == "false"


def test_subject_without_audit_rows_still_completes():
    sql = FakeSql([req()], traces=(), originals={}, pii=0)
    proc, _ = make(sql)
    res = proc.process_pending()
    assert res["completed"] == 1
    assert not [t for t, p in sql.inserts if t.startswith("shopping_assistant.raw_logs.")]


def test_other_catalog_refused():
    try:
        make(FakeSql([]), catalog="sandbox")
    except ValueError:
        return
    raise AssertionError("catalog guard missing")


def test_vacuum_waits_for_window_and_only_pii_tables():
    old = ts(NOW - timedelta(days=9))
    recent = ts(NOW - timedelta(days=2))
    # recent completion: not yet due
    sql = FakeSql([], completed=[["r1", "GDPR", recent]])
    proc, _ = make(sql)
    assert proc.vacuum_completed() == {"vacuumed": 0}
    assert not sql.statements("VACUUM")
    # old completion: due
    sql = FakeSql([], completed=[["r1", "GDPR", old]])
    proc, _ = make(sql)
    assert proc.vacuum_completed() == {"vacuumed": 2}
    vac = [s for s, _p in sql.statements("VACUUM")]
    assert vac == ["VACUUM shopping_assistant.raw_logs.customer_pii RETAIN 168 HOURS",
                   "VACUUM shopping_assistant.raw_logs.feedback_pii RETAIN 168 HOURS"], vac
    logs = [p for t, p in sql.inserts if t.endswith("retention_enforcement_log")]
    assert len(logs) == 2 and all(p["vacuum_type"] == "pii_erasure_vacuum" and p["status"] == "success" for p in logs)


def test_vacuum_dry_run_and_not_repeated():
    old = ts(NOW - timedelta(days=9))
    sql = FakeSql([], completed=[["r1", "GDPR", old]])
    proc, _ = make(sql, execute=False)
    proc.vacuum_completed()
    assert not sql.statements("VACUUM") and not sql.statements("INSERT")
    # already vacuumed after that completion: nothing to do
    sql = FakeSql([], completed=[["r1", "GDPR", old]], last_vacuum=ts(NOW - timedelta(days=1)))
    proc, _ = make(sql)
    assert proc.vacuum_completed() == {"vacuumed": 0}


def test_vacuum_never_below_seven_days():
    sql = FakeSql([], policy=[["GDPR", "30", "1"]], completed=[["r1", "GDPR", ts(NOW - timedelta(days=3))]])
    proc, _ = make(sql)
    proc.vacuum_completed()
    assert all("RETAIN 168 HOURS" in s for s, _p in sql.statements("VACUUM"))


def test_app_id_list_parsing():
    assert aw._split_app_ids("myre_app, ecom-shop-assistant-dev,myre_app,") == ["myre_app", "ecom-shop-assistant-dev"]
    assert aw._split_app_ids("") == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:                      # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
