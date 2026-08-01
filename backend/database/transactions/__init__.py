"""No multi-document transactions exist in this codebase yet -- every write
today is either a single `update_one`/`insert_one` or, where multiple
documents must move together (see `database.migrations`), an idempotent
upsert that is safe to re-run instead of an atomic transaction. This
package is reserved for when a real cross-collection transaction is
needed (Motor/PyMongo's `client.start_session()` + `with_transaction`
against the replica-set `settings.mongo_uri` already points at).
"""
