"""Phase 36 validation script — run once, reads no registry files directly."""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

def call(path, method="GET", body=None):
    data = None
    hdr = {}
    if body is not None:
        data = json.dumps(body).encode()
        hdr = {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=hdr)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

results = {}

# 1. Stats
st, stats = call("/stats")
results["stats"] = f"{st} users={stats['total_users']} families={stats['total_families']}"

# 2. Registrations (get two users for the test pair)
st2, regs = call("/registrations")
if not isinstance(regs, list) or len(regs) < 2:
    print("ERROR: need at least 2 users")
    sys.exit(1)
req_uid = regs[0]["user_id"]
tgt_uid = regs[1]["user_id"]
results["requester"] = req_uid
results["target"] = tgt_uid

# 3. Create connection request with note field
st3, resp3 = call("/family-connection/request", "POST", {
    "requester_user_id": req_uid,
    "target_user_id": tgt_uid,
    "relationship_guess": "sibling",
    "preferred_contact_method": "phone",
    "note": "Phase36 validation test",
    "search_context": {
        "source": "find_family",
        "confidence_score": 85,
        "confidence_level": "high",
        "reason_summary": "validation",
    },
})
req_id = resp3.get("request_id", "")
results["create_request"] = f"{st3} success={resp3.get('success')} status={resp3.get('status')} id={req_id}"

# 4. Duplicate blocked
st4, resp4 = call("/family-connection/request", "POST", {
    "requester_user_id": req_uid,
    "target_user_id": tgt_uid,
    "preferred_contact_method": "email",
})
dup_msg = str(resp4.get("message", ""))
results["duplicate_block"] = f"{st4} success={resp4.get('success')} already_pending={'already pending' in dup_msg}"

# 5. Request in admin list with note
st5, all_reqs = call("/connection-requests")
match = next((r for r in all_reqs if r.get("request_id") == req_id), None)
results["admin_list"] = f"{st5} found={match is not None} note={match.get('note') if match else 'N/A'} status={match.get('status') if match else 'N/A'} masked_name={match.get('target_masked_name') if match else 'N/A'}"

# 6. Activity log events
st6, log = call("/activity-log?limit=100")
event_types = [e.get("event_type") for e in (log if isinstance(log, list) else [])]
results["activity_log"] = f"{st6} created={'family_connection_request_created' in event_types} dup_blocked={'family_connection_request_duplicate_blocked' in event_types}"

# 7. Data guard still active
st7, safety = call("/registry-safety")
results["data_guard"] = f"{st7} status={safety.get('data_guard_status')} users={safety.get('total_users')} families={safety.get('total_families')}"

# 8. failed event (invalid users)
st8, resp8 = call("/family-connection/request", "POST", {
    "requester_user_id": "usr_notexist1",
    "target_user_id": "usr_notexist2",
    "preferred_contact_method": "phone",
})
st9, log2 = call("/activity-log?limit=5")
event_types2 = [e.get("event_type") for e in (log2 if isinstance(log2, list) else [])]
results["failed_event"] = f"api={st8} logged={'family_connection_request_failed' in event_types2}"

print("\n=== PHASE 36 VALIDATION ===")
for k, v in results.items():
    print(f"  {k}: {v}")
print("\nVALIDATION_COMPLETE")
