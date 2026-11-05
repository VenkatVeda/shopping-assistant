"""
Tests for the Level-1 redaction in audit_wrapper.py (no network, no Databricks).
The query is redacted once at the door (app.py::search), so these tests cover both
what must be redacted and what must come through unchanged.

    python tests/test_redaction.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import audit_wrapper as aw  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


r = aw.redact_query


def kinds(text):
    return aw.redact_query_report(text)[1]


# ── phone numbers: every common way of writing one ────────────────────────
for text, want in [
    ("phone 9876543210", "phone [PHONE]"),
    ("phone +91 8451245789", "phone [PHONE]"),
    ("phone +91 84512 45789", "phone [PHONE]"),
    ("+91-8451245789", "[PHONE]"),
    ("+919876543210", "[PHONE]"),
    ("919876543210", "[PHONE]"),
    ("91 8451245789", "[PHONE]"),
    ("09876543210", "[PHONE]"),
    ("0 98765 43210", "[PHONE]"),
    ("0091 98765 43210", "[PHONE]"),
    ("98765 43210", "[PHONE]"),
    ("98765-43210", "[PHONE]"),
    ("987-654-3210", "[PHONE]"),
    ("98-7654-3210", "[PHONE]"),
    ("(98765) 43210", "[PHONE]"),
    ("call 555-123-4567", "call [PHONE]"),
    ("call (555) 123-4567", "call [PHONE]"),
    ("555.123.4567", "[PHONE]"),
    ("+1 (555) 123-4567", "[PHONE]"),
    ("+44 7911 123456", "[PHONE]"),
    ("07911 123456", "[PHONE]"),
    ("011 23456789", "[PHONE]"),
    ("022-12345678", "[PHONE]"),
    ("9 8 7 6 5 4 3 2 1 0", "[PHONE]"),
    ("nine eight seven six five four three two one zero", "[PHONE]"),
    ("my number is 12345678", "my number is [PHONE]"),
    ("mobile: 8765432", "mobile: [PHONE]"),
    ("whatsapp me on +919876543210", "whatsapp me on [PHONE]"),
    ("reach me at 98765 43210 pls", "reach me at [PHONE] pls"),
    ("०९८७६५४३२१", "[PHONE]"),   # Devanagari digits
]:
    check(f"phone: {text!r}", r(text) == want)

# ── email and payment handles ─────────────────────────────────────────────
for text, want in [
    ("mail me at john.doe@gmail.com", "mail me at [EMAIL]"),
    ("john.doe+tag@sub.example.co.in", "[EMAIL]"),
    ("diya @gmail.com", "[EMAIL]"),
    ("diya @ gmail.com", "[EMAIL]"),
    ("diya [at] gmail [dot] com", "[EMAIL]"),
    ("diya at gmail dot com", "[EMAIL]"),
    ("a_b-c@x.org.", "[EMAIL]."),
    ("diya​@gmail.com", "[EMAIL]"),          # hidden zero-width character
    ("diya@okaxis", "[UPI]"),
    ("pay me at diya@ybl", "pay me at [UPI]"),
]:
    check(f"email/upi: {text!r}", r(text) == want)

# ── Aadhaar, cards, tax and bank ids ──────────────────────────────────────
for text, want in [
    ("aadhaar 1234 5678 9012", "aadhaar [AADHAAR]"),
    ("aadhar no: 123456789012", "aadhar no: [AADHAAR]"),
    ("my uid is 2345-6789-0123", "my uid is [AADHAAR]"),
    ("1234 5678 9012", "[AADHAAR]"),
    ("123456789012", "[AADHAAR]"),
    ("card 4111 1111 1111 1111", "card [CARD]"),
    ("4111111111111111", "[CARD]"),
    ("5500 0000 0000 0004", "[CARD]"),
    ("3782 822463 10005", "[CARD]"),
    ("card number is 4111-1111-1111-1111 cvv 123 expiry 12/27", "card number is [CARD] cvv [CVV] expiry [CARD_EXPIRY]"),
    ("pan ABCDE1234F", "pan [PAN]"),
    ("pan abcde1234f", "pan [PAN]"),
    ("ssn 123-45-6789", "ssn [SSN]"),
    ("ssn 123456789", "ssn [SSN]"),
    ("GSTIN 22AAAAA0000A1Z5", "GSTIN [GSTIN]"),
    ("ifsc SBIN0001234", "ifsc [IFSC]"),
    ("account number 123456789012", "account number [BANK_ACCOUNT]"),
    ("a/c no 50100123456789", "a/c no [BANK_ACCOUNT]"),
    ("IBAN GB82 WEST 1234 5698 7654 32", "IBAN [IBAN]"),
    ("passport K1234567", "passport [PASSPORT]"),
    ("voter id ABC1234567", "voter id [VOTER_ID]"),
    ("driving licence MH1220110062821", "driving licence [DRIVING_LICENCE]"),
    ("MH12AB1234", "[VEHICLE_NO]"),
    ("dob 12/05/1990", "dob [DOB]"),
    ("date of birth: 5 May 1990", "date of birth: [DOB]"),
    ("born in 1990", "born in [DOB]"),
    ("pincode 400001", "pincode [PINCODE]"),
    ("zip 94105", "zip [PINCODE]"),
    ("ip 192.168.1.10", "ip [IP]"),
    ("my address is 12 MG Road, Bandra, Mumbai", "my address is [ADDRESS]"),
    ("I live at flat 5 Sunrise Apartments", "I live at [ADDRESS]"),
]:
    check(f"id: {text!r}", r(text) == want)

# ── names ─────────────────────────────────────────────────────────────────
for text, want in [
    ("my name is Tina and I want a bag", "my name is [NAME] and I want a bag"),
    ("my name is diya and I need a bag", "my name is [NAME] and I need a bag"),
    ("my name is Mary Anne Smith", "my name is [NAME]"),
    ("I am Nupur Kawade, find a bag", "I am [NAME], find a bag"),
    ("I am Diya Show me bags", "I am [NAME] Show me bags"),
    ("MY NAME IS Tina", "MY NAME IS [NAME]"),
    ("i am ahmed, show bags", "i am [NAME], show bags"),
    ("Hi I  am venkat and I need black tote bags", "Hi I  am [NAME] and I need black tote bags"),
    ("myself Diya here", "myself [NAME] here"),
]:
    check(f"name: {text!r}", r(text) == want)

# ── the example that started this: lowercase name, spaced email ───────────
check("lowercase name, phone and a space before the @ in one query",
      r("I am diya my no. is 8788956485 and my e mail id is diya @gmail.com show me party bags")
      == "I am [NAME] my no. is [PHONE] and my e mail id is [EMAIL] show me party bags")
check("several kinds in one query",
      r("my name is Tina and email id is tina@gmail.com, phone +91 84512 45789, show me pink bags")
      == "my name is [NAME] and email id is [EMAIL], phone [PHONE], show me pink bags")

# ── must come through unchanged (ordinary shopping text, ids, prices, dates) ──
for text in [
    "I am looking for a black leather tote",
    "i am searching for a wallet under 100",
    "hi, I'm looking for a backpack",
    "this is a gift for my mom, show me bags",
    "bags between 200 and 300 dollars",
    "black tote under $250",
    "size 15 inch laptop bag 2026-10-26",
    "show me the 3 best options in brown",
    "Steve Madden shoulder bag $224.96",
    "Can i get some more bags",
    "product 260427760 price 224.96 score 0.64353704",
    "ID: 247464580, ID: 214633120",
    "sizes 6 7 8 9 10 11 12",
    "bag @ 200 dollars",
    "3 black cross body bags",
    "5 Dr Martens boots",
    "I am in need of a bag",
    "i am interested in bags",
    "i am vegetarian and want a bag",
    "call me tomorrow",
    "call me back later",
    "show me 2 bags in 15 colours",
    "my number of bags is 3",
    "2026-10-14 10:10:49.935",
    "I need it by 12/05/2026",
    "I am Looking for a bag",
]:
    check(f"unchanged: {text}", r(text) == text)

# ── behaviour of the helpers ──────────────────────────────────────────────
check("empty and None give an empty string", r("") == "" and r(None) == "")
for text in [
    "my name is Tina, a@b.com 9876543210",
    "I am diya my no. is 8788956485 and my e mail id is diya @gmail.com",
    "card 4111 1111 1111 1111 cvv 123 aadhaar 1234 5678 9012 pan ABCDE1234F",
    "ssn 123-45-6789 and 9876543210 and diya@ybl",
]:
    once = r(text)
    check(f"redacting twice changes nothing: {text[:40]}", r(once) == once)
check("placeholders are not matched again, and do not act as keywords",
      r("[EMAIL] [PHONE] [NAME] [CARD] [AADHAAR]") == "[EMAIL] [PHONE] [NAME] [CARD] [AADHAAR]"
      and r("[PHONE] 123-45-6789") == "[PHONE] [SSN]")
check("_detect_pii_types still reports the kinds found",
      set(aw._detect_pii_types("a@b.com 9876543210 123-45-6789")) >= {"EMAIL", "PHONE", "SSN"})
check("a card is labelled CARD only, not also PHONE or AADHAAR", kinds("card 4111 1111 1111 1111") == ["CARD"])
check("an Aadhaar number is labelled AADHAAR only", kinds("aadhaar 1234 5678 9012") == ["AADHAAR"])
uuid = "0f8fad5b-d9cb-469f-a165-70867728950e"
check("UUIDs still survive the keep-ids variant", aw._redact_pii_keep_ids(f"{uuid} mail a@b.com") == f"{uuid} mail [EMAIL]")
digit_uuid = "85559270-7175-4a7f-b98c-776275462912"
check("a UUID whose last part is all digits survives the keep-ids variant",
      aw._redact_pii_keep_ids(f"'trace_id': '{digit_uuid}'") == f"'trace_id': '{digit_uuid}'")
check("a UUID that starts with 00 is not read as an international phone number",
      "[PHONE]" not in r("'session_id': '00972333-f3d7-48ed-9978-1219541dfd35'"))
check("redaction is fast on a long text (4000 characters, many numbers)",
      (lambda t0: (r(("Steve Madden bag 224.96 id 260427760 " * 110)[:4000]), time.time() - t0)[1] < 1.0)(time.time()))

# ── redact_query_report: which kinds were found (labels only) ─────────────
text, types = aw.redact_query_report("my name is Tina, mail a@b.com, phone +91 84512 45789")
check("report returns the redacted text and the kinds found, sorted",
      text == "my name is [NAME], mail [EMAIL], phone [PHONE]" and types == ["EMAIL", "NAME", "PHONE"])
check("a clean query reports no kinds and is unchanged",
      aw.redact_query_report("black leather tote under $250") == ("black leather tote under $250", []))
check("redacted text from the report equals redact_query", aw.redact_query_report("call 555-123-4567")[0] == r("call 555-123-4567"))
check("empty and None give ('', [])", aw.redact_query_report("") == ("", []) and aw.redact_query_report(None) == ("", []))
check("the new kinds are reported by name",
      kinds("aadhaar 1234 5678 9012, dob 12/05/1990, upi diya@ybl, passport K1234567")
      == ["AADHAAR", "DOB", "PASSPORT", "UPI"])

# ── log_level1_redaction: two rows, written immediately, labels only ──────
import json  # noqa: E402

w = object.__new__(aw.AuditWrapper)
w.app_id, w.catalog, w.schema_version, w._key_cache = "app", "cat", "1.0", {}
rows = []
aw._fire = lambda table, row, on_failure=None: rows.append((table, row))
w.log_level1_redaction("trace-1", "my name is [NAME], mail [EMAIL]", ["EMAIL", "NAME"], subject_ref="sref")
tables = [t for t, _ in rows]
check("one node row and one guardrail row, both under the same trace",
      tables == ["cat.raw_logs.node_executions_raw", "cat.raw_logs.guardrail_results_raw"]
      and all(row["trace_id"] == "trace-1" for _, row in rows))
node, guard = rows[0][1], rows[1][1]
check("node row: named level1_redaction, redacted query as input_summary",
      node["node_name"] == "level1_redaction" and node["node_type"] == "guardrail"
      and node["input_summary"] == "my name is [NAME], mail [EMAIL]" and node["subject_ref"] == "sref")
nmeta = json.loads(node["node_metadata"])
check("node metadata lists the kinds found, not the values",
      nmeta == {"level": 1, "pii_types_found": ["EMAIL", "NAME"], "query_changed": True})
check("guardrail row: policy name, result 'redacted', never blocks, no invented score",
      guard["policy_name"] == "level1_pii_redaction" and guard["result"] == "redacted"
      and guard["triggered_block"] == "false" and guard.get("score") is None and json.loads(guard["guardrail_metadata"])["pii_types_found"] == ["EMAIL", "NAME"])
rows.clear()
w.log_level1_redaction("trace-2", "black tote", [])
check("a clean query is recorded as 'clean' with an empty list",
      rows[1][1]["result"] == "clean" and json.loads(rows[0][1]["node_metadata"])["query_changed"] is False)
rows.clear()
w.log_level1_redaction("trace-3", "mail a@b.com", ["EMAIL"])
check("the stored query is masked again even if a caller passes unredacted text", "a@b.com" not in rows[0][1]["input_summary"])

print("\nRESULT:", "ALL PASSED" if not failures else f"FAILED: {failures}")
sys.exit(1 if failures else 0)
