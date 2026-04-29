# Render Admin Passcode Setup

Set `ADMIN_PASSCODE` in Render so private family data stays locked for public traffic.

## Where to set it

1. Open your Render service dashboard.
2. Go to **Environment**.
3. Add variable:
   - Key: `ADMIN_PASSCODE`
   - Value: a strong passcode (minimum 16+ characters recommended)
4. Save changes and redeploy.

## Behavior

- If `ADMIN_PASSCODE` is set, admin login uses that value.
- If it is not set, local fallback `local-dev-admin-passcode` is used.
- Never rely on fallback in production.

## Frontend usage

- Public users only see safe pages and aggregate stats.
- Use the **Admin Login** button in the footer to unlock private panels.
