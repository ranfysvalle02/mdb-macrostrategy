"""The search angle: PostgreSQL (tsvector + pgvector) vs MongoDB (Atlas Search +
$vectorSearch) on the same record.

Self-contained app folder (its own docker-compose stack, CLIs, dashboard, and
tests). Imports only the engine-agnostic helpers from the top-level ``shared``
package.
"""
