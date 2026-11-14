# Rules

## Database environment variables

Only these two names. No aliases, no fallbacks:

- `CURRENT_PRODUCTION_DB_URL` — source database (read-only)
- `NEW_DATABASE_URL` — target database (destination)

Do not add another name for these values anywhere in the code.
