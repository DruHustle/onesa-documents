# Onesa Documents

One private repository owns two independently released editions. Public material includes Developer Docs and other approved external publications. Internal collections include Engineering, HR, operations and other company records. Both editions use the user's original document URLs where supported.

| Edition | Canonical origin | Release and access |
| --- | --- | --- |
| Public Documents | https://docs.onesa.eu | Standalone static files. Every published guide and download is available without sign-in. |
| Internal Documents | https://docs.internal.onesa.eu | Authenticated web application, document API, private metadata and attachment storage. A document-specific permission is required on every access. |

Use the same document path under the internal origin when updating an internal bookmark. The public build reads only approved public inputs. The internal service owns its private database and attachments.

## Public build

The imported public release reproduces the existing site's approved developer guides, search index, diagrams, PDF downloads, images, and front-end assets. `public/release.json` binds the source deployment and every public file to a digest. `python public/tools/build.py /tmp/onesa-public-dist` produces a fresh static root with no need for Portal, Identity, the internal service or the original hosting provider. Use the `public` project root and its `vercel.json` for preview/production. No catch-all should turn a missing internal URL into a successful public response. Additional approved external publications use explicit title, slug, source checksum and format entries in `public/publications.json`; publish only reviewed external sources. Store limited or private documents solely in the internal store.

## Internal pilot

Install `python -m pip install -r requirements.txt` with Python 3.12 and run `python -m unittest discover -s tests -v`. Start using `uvicorn internal_documents.app:create --factory --host 127.0.0.1 --port 8000 --no-access-log` behind TLS, or build the repository Dockerfile. Mount a private encrypted persistent volume at `/srv/docs`. It contains server-side sessions, ACLs, drafts and attachments. Keep it and its backups outside source control and the public deployment. Configure access logs to omit callback query strings.

| Configuration | Staging / production settings |
| --- | --- |
| DOCS_ISSUER | Exact HTTPS Auth0 custom domain. Production issuer is `https://auth.onesa.eu/`. |
| DOCS_AUDIENCE | Dedicated resource identifier `https://api.docs.onesa.eu`. This identifier does not act as a content origin. |
| DOCS_CLIENT_ID, DOCS_CLIENT_SECRET | Confidential web client registered only for the internal product and its exact environment-specific callback. |
| DOCS_ORIGIN | Production is `https://docs.internal.onesa.eu`. Use a protected separate hostname for staging. |
| DOCS_ENVIRONMENT | Set to `production` on the production instance to enforce both canonical origins. |
| DOCS_DATA | Private writable directory for document data and the issuer-specific public signing-key cache. |

The sign-in flow uses Authorization Code with S256 PKCE, nonce and one-use browser-bound state. Signed access tokens must match the configured issuer and resource. The Onesa UUID and scoped products/roles are namespaced claims. The canonical grant is `internal-documents`. Previously approved `engineering-documents` identities remain eligible **only for the engineering collection** and still require per-document ACLs. Search, metadata, preview, drafts and attachment downloads apply the same restriction. HR access requires an explicit ACL plus canonical Internal Documents access. Portal and Identity administrators inherit no document grants.

## Inventory, import and recovery

`inventory/engineering-material.csv` identifies 553 Git-tracked document candidates from nine Onesa repositories by full commit, path and blob hash. It grants neither editorial approval nor reader access. Reconcile protected engineering material, HR, operations, other departmental sources, effective ACLs, cloud storage, aliases and approved versions separately with their owners. Preserve source authorship, stable document/revision/attachment IDs, bookmarks and tenant ACLs. Do not put any private content into this repository, including the HR index at record level.

Import an owner-reviewed manifest and objects with `python -m internal_documents DATABASE MANIFEST --sha256 EXPECTED_HASH`. Supply original IDs, document `collection`, product and discipline, original author, bookmark, revisions, approvals, original attachment IDs and digests and reviewed user ACLs. Store attachment bytes alongside the manifest as `objects/<sha256>`. The importer operates transactionally, checks independent counts and file hashes, and rejects conflicting existing records. Legacy schema-1 engineering exports without a collection migrate to engineering; new non-engineering exports must set an explicit collection. Replaying the same manifest leaves subsequent local drafts intact. Git provenance links identify immutable source revisions. Unsupported ACLs or record types need explicit preservation before any real migration.

Access tokens expire as issued. Cache recognized issuer signing keys for up to 24 hours since a successful fetch, with normal refresh every five minutes. An API restart can use those keys. A missing key or expired access token denies access. On expiry a new authorization flow restores the original bookmark. An editor with unsaved form content can sign in in a second tab and submit again. Removing an object ACL affects the next request. Identity changes affect future issuance and any existing self-contained access at its bounded expiry.

Before cutover, restore the private volume to a separate environment and reconcile original and post-backup records, revisions, drafts, ACLs, and attachment hashes. Qualify current-reader/editor/admin workflows, restricted search/downloads, public unsigned bookmarks, and migrated bookmark paths on the internal hostname with real controlled accounts in staging, then repeat applicable production checks. Container smoke tests and local synthetic fixtures establish implementation behavior only.
