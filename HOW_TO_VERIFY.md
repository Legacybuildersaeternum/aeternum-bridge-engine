# Aeternum Bridge Verification Guide

## Phase 37: User Trust + Entry Agreement Layer

Use this checklist to verify the Phase 37 trust and consent layer while preserving Phase 35 and Phase 36 behavior.

### 1. Start server

```bash
cd /Users/legacybuildersaeternum/Documents/aeternum-bridge-engine
.venv/bin/python -m uvicorn main:app --app-dir /Users/legacybuildersaeternum/Documents/aeternum-bridge-engine --host 127.0.0.1 --port 8000
```

Open:

- http://127.0.0.1:8000/

### 2. Confirm all tabs load

Visit each tab and confirm no render errors:

- Home
- Register
- Family Tree
- Find Family
- Activity Log
- Export & Backup
- Admin Dashboard

### 3. Register page: agreement block checks

On Register:

1. Fill required identity fields.
2. Leave "I understand and agree..." unchecked.
3. Click Submit Registration.
4. Expected: clear error, submission blocked.

Then:

1. Check agreement.
2. Leave "Return / Reconnection Interest" unselected.
3. Click Submit Registration.
4. Expected: clear error, submission blocked.

### 4. Register page: successful submit check

On Register:

1. Fill required fields.
2. Check agreement checkbox.
3. Pick Return / Reconnection Interest.
4. (Optional) check ecosystem updates opt-in.
5. Submit.
6. Expected: success banner with user_id and family_id.

### 5. Backend persistence check for new fields

Use the API to inspect the new user record via registrations endpoint.

Expected fields present in user profile:

- entry_agreement_accepted: true
- entry_agreement_accepted_at: ISO timestamp
- ecosystem_updates_opt_in: true/false
- return_reconnection_interest: selected category value

### 6. Admin Dashboard trust counts

On Admin Dashboard, confirm summary/stat surfaces now include:

- Entry Agreement Accepted count
- Ecosystem Updates Opt-ins count
- Return/Reconnection category counts (Yes/Maybe/No)

### 7. Activity Log events

Open Activity Log and confirm events exist for new registration:

- entry_agreement_accepted
- ecosystem_updates_opted_in (only when opt-in checked)
- return_reconnection_interest_recorded

### 8. Refresh stability check

Refresh browser and confirm:

- App still loads
- Existing data remains intact
- New registration persists

### 9. Phase 35 guard check

Call registry safety endpoint and confirm data guard remains active.

```bash
curl -s http://127.0.0.1:8000/registry-safety
```

Expected:

- data_guard_status is ACTIVE
- Users/families are non-zero and stable

### 10. Phase 36 flow regression check

Verify family connection request flow still works:

- From Register page family lookup and Find Family results, Request Connection opens confirmation modal.
- Modal requires contact method.
- Sending request succeeds and returns pending_outside_verification.
- Duplicate request attempt is safely blocked.

### 11. Git safety check before commit

Before commit/push, verify no runtime data files are staged:

```bash
git status --short
```

Do not stage/commit:

- data/diaspora_registry.json
- data/diaspora_registry_backup.json
- data/registry_backups/*
