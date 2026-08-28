# MediGrid

MediGrid: A real-time hospital operations command center. Ingests fragmented HIS/EMR data and uses a rules-based reconciliation engine to present a unified, accurate view of patient flow and bed capacity.

Healthcare operations reconciliation dashboard.

## Local development

1. Create a virtual environment.
2. Install dependencies:
   pip install -r requirements.txt
3. Copy `.env.example` to `.env` and set your Postgres connection string.
4. Run the app:
   flask --app app run

## Production / Vercel

This project is configured for Vercel using `vercel.json` and the `@vercel/python` builder.

Required environment variable:
- `DATABASE_URL` = Postgres connection string

The app keeps SQLite as a local fallback if `DATABASE_URL` is not set.
