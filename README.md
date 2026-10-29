# ChargeNeighbour — Flask + PostgreSQL + CARTO basemap

A deployable starter marketplace for peer-to-peer EV charger reservation.

## Features
- Separate renter / EV user registrations, hashed passwords and secure session cookies
- Renter listing creation with coordinates, connector, kW, hourly price, enable/disable listing
- Public search by area and Leaflet map with CARTO basemap
- Registered users reserve future time slots; overlap detection under a PostgreSQL row lock
- User and renter dashboards, booking cancellations, CSRF protection and auth throttling
- PostgreSQL only; no silent production SQLite fallback

## Deploy on Render
1. Put all files in the repository root and push this folder to a GitHub repository.
2. In Render choose **New > Blueprint**, connect the repository and apply `render.yaml`.
3. Set `CARTO_BASEMAP_KEY` to your **CARTO Basemaps** key. For a CARTO Maps API data token, the basemap endpoint may require a separate key.
4. Render provisions a PostgreSQL database and Flask web service, sets `DATABASE_URL` and generates `SECRET_KEY`. First startup runs `flask --app app init-db`.
5. Visit the assigned `https://...onrender.com` URL, register a renter and user and test bookings.

**Note:** the YAML uses paid `starter` web and `basic` database plans. Check availability/pricing before deployment. For existing services, manually configure `DATABASE_URL`, `SECRET_KEY`, `CARTO_BASEMAP_KEY`, `COOKIE_SECURE=1`, `BEHIND_PROXY=1`; build `pip install -r requirements.txt`; start `flask --app app init-db && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`.

## Local setup
- Python 3.11+ and PostgreSQL required.
- `python -m venv .venv` then activate and `pip install -r requirements.txt`.
- Copy `.env.example` to `.env`; provide your PostgreSQL URL and key.
- `flask --app app init-db`
- `python app.py`
- Visit `http://127.0.0.1:5000`.

## Intentional MVP limitations / before real customers
- **No payments, payouts, charging hardware integration, identity verification, insurance, refunds, or real-time charger status.** “Booking confirmed” reserves an app slot only.
- **UTC input** on booking screen; add timezone-aware local time conversion for launch.
- Listing locations are public; allow approximate public pins and privately deliver exact access details after verification.
- Add email verification, password reset, audit logs, moderation, renter ownership validation, notifications, GDPR/DPDP policy, managed Redis for distributed rate limits, and production monitoring.
- `create_all()` is bootstrap-only, not migrations. Add Alembic/Flask-Migrate before schema changes.
- Front-end map is sourced from CARTO basemap tiles; charger data is in your PostgreSQL database, not in CARTO. Check CARTO key type, usage restrictions, domain whitelist and pricing.
- If hosts can change availability while bookings exist, reservation history remains intact; disabled listings do not accept new reservations.
- Long-term production concurrency hardening: add PostgreSQL exclusion constraint on time ranges (to protect against non-application writes).
