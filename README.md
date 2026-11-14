# Onesa Engineering Documents

Independent web application, API and private document store for engineering records. Existing Portal sessions, Portal roles and public Developer Docs are outside this application. The existing engineering hostname and Cloudflare Access remain the live site until the private pilot has passed. `docs.onesa.eu` currently serves public Developer Docs and must retain its public URLs through any later domain transition.

## Run an isolated pilot

Use Python 3.12, install `python -m pip install -r requirements.txt` and run `python -m unittest discover -s tests -v`. Start the server with `uvicorn engineering_docs.app:create --factory --host 127.0.0.1 --port 8000 --no-access-log` behind an HTTPS reverse proxy. The container uses the same entry point. Deploy a single instance with a private encrypted persistent volume mounted at `/srv/docs`. Its database contains server-side sessions, private attachments, ACLs and unpublished text. Keep that volume, its backups and restore keys outside the image and Git. Access logs must omit OIDC callback query strings.

| Configuration | Meaning |
| --- | --- |
| DOCS_ISSUER | Exact HTTPS Auth0 custom-domain origin for this environment |
| DOCS_AUDIENCE | Dedicated Docs API identifier, intended production value `https://api.docs.onesa.eu` |
| DOCS_CLIENT_ID / DOCS_CLIENT_SECRET | Environment-specific confidential web application with exact callback `DOCS_ORIGIN/callback` |
| DOCS_ORIGIN | HTTPS origin an operator has assigned exclusively to this deployment |
| DOCS_DATA | Private writable volume containing the service database and the issuer's public signing-key cache |

The web client uses Authorization Code, S256 PKCE, a nonce and a one-use browser-bound state. Access tokens must be signed RS256 for the configured issuer and resource. The Identity policy emits a stable UUID and audience-scoped products and roles under `https://onesa.eu/claims/`. Engineering Document access requires product `engineering-documents` and an explicit per-document ACL. Draft edits additionally require a Docs editor or admin role. A Portal administrator receives no implicit document grant.

## Import and reconcile

The existing AI repository owns the public and private documentation build inputs. Keep its original authoring systems and IDs. The first importer takes an operator-reviewed export and imports only new records. Provide the old stable document ID, exact existing bookmark path, original author IDs, revisions and revision authors, current approved revision, effective document ACLs, plus each attachment's original ID and SHA-256. Resolve source policy and map stable principals before export. Unsupported ACLs, non-document landing pages, legacy aliases, diagrams and protected source material require explicit mapping. The importer does not infer records or permissions from an email address, filename, a public stub, or a product membership. Withdrawn attachments must be omitted or separately reviewed. A change to the snapshot of an existing ID raises a conflict.

Use `python -m engineering_docs /srv/docs/documents.sqlite /private/manifest.json --sha256 EXPECTED_HASH`. The full format and exact independent counts are exercised in `tests/test_journeys.py`. Store attachment bytes as `objects/<sha256>` beside the manifest. Pin every Git source URL to its immutable commit. Review the exported approved bytes and per-object access with each owner before importing a real record. The catalog permits search, bookmark previews, direct attachment downloads, draft creation and explicit per-user access on the owned database. Import is transactional, verifies bytes, and repeats safely; private documents and attachments never go in this repository. The checked-in source inventory indexes candidate files by product and discipline; it grants no publication or document access.

## Outage behavior and release

Known issuer keys remain usable across process restarts for at most 24 hours from a successful fetch; normal refresh is every five minutes. Unknown keys fail closed. Using the last authenticated key never extends JWT expiry. The current client has no refresh token. After access-token expiry, a new authorization flow returns the user to the document. Account suspension and grant removal at the issuer prevent newly issued access; already-issued access may last until token expiry. Removing the document ACL takes effect at the next request. Expired draft submissions keep their text in the browser response and allow sign-in in another tab.

Build and stage this service independently of Portal. Use a separate test hostname and provider tenant, copy approved engineering material into its private volume, and reconcile both source and target totals and hashes. Test search, downloads, previews and editor workflows with a Docs editor, ordinary user, Portal admin and Identity admin. Compare representative legacy bookmarks using real approved records, and repeat with Portal unavailable. Check API restart with cached keys, unknown keys, and expired tokens. Back up the volume. Restore to a separate volume and compare counts, digests and post-backup draft, ACL and attachment changes before selecting the serving origin. Until these controls pass on the actual environment, continue serving existing production sites and the existing engineering entry.

The Docs icon is reused from `DruHustle/onesa-ai-platform`, commit `8e44eff0765dca5d6f0685e234f784887593e968`, `onesa-ai-platform-frontend/apps/docs/static/brand/icons/v1/onesa-docs.svg`. No private handbook content has been copied from that repository.
