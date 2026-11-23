# Onesa Documents

This public source repository owns two independently released editions. Internal document bytes, users, access-control lists, session records, databases and secrets must remain in private storage, never in this public repository or public artifact. Public material includes Developer Docs and other approved external publications. Internal collections include Engineering, HR, operations and other company records. Both editions use the user's original document URLs where supported.

| Edition | Canonical origin | Release and access |
| --- | --- | --- |
| Public Documents | https://docs.onesa.eu | Standalone static files. Every published guide and download is available without sign-in. |
| Internal Documents | https://docs.internal.onesa.eu | Authenticated web application, document API, private metadata and attachment storage. A document-specific permission is required on every access. |

Use the same document path under the internal origin when updating an internal bookmark. The public build reads only approved public inputs. The internal service owns its private database and attachments.

## Public build

The imported public release reproduces the existing site's approved developer guides, search index, diagrams, PDF downloads, images, and front-end assets. `public/release.json` binds the source deployment and every public file to a digest. `python public/tools/build.py /tmp/onesa-public-dist` produces a fresh static root with no need for Portal, Identity, the internal service or the original hosting provider. Use the `public` project root and its `vercel.json` for preview/production. No catch-all should turn a missing internal URL into a successful public response. Additional approved external publications use explicit title, slug, source checksum and format entries in `public/publications.json`; publish only reviewed external sources. Store limited or private documents solely in the internal store.

## Internal pilot

Install `python -m pip install -r requirements.txt` with Python 3.12 and run `python -m unittest discover -s tests -v`. Start using `uvicorn internal_documents.app:create --factory --host 127.0.0.1 --port 8000 --no-access-log` behind TLS, or build the repository Dockerfile. Mount a private encrypted persistent volume at `/srv/docs`. `/healthz` is process liveness only; `/readyz` checks the private SQLite schema and write-lock availability and returns 503 if the persistent store is missing or unavailable. The Docker image uses `/readyz` for its container health check. It contains server-side sessions, ACLs, drafts and attachments. Keep it and its backups outside source control and the public deployment. Configure access logs to omit callback query strings.

| Configuration | Staging / production settings |
| --- | --- |
| DOCS_ISSUER | Exact HTTPS Auth0 custom domain. Production issuer is `https://auth.onesa.eu/`. |
| DOCS_AUDIENCE | Dedicated resource identifier `https://api.docs.onesa.eu`. This identifier does not act as a content origin. |
| DOCS_CLIENT_ID, DOCS_CLIENT_SECRET | Confidential web client registered for the internal product and its exact environment-specific callback. Set the secret directly or supply its file path in `DOCS_CLIENT_SECRET_FILE`. |
| DOCS_ORIGIN | Production is `https://docs.internal.onesa.eu`. Use a protected separate hostname for staging. |
| DOCS_ENVIRONMENT | The Docker image defaults to `production` and enforces the two canonical production origins. Explicitly override to `staging` only on isolated nonproduction deployments with their own OIDC client, hostname, and private data. |
| DOCS_DATA | Private writable directory for document data and the issuer-specific public signing-key cache. The service enforces directory mode `0700` and SQLite database mode `0600`; startup fails if those restrictions cannot be applied. |

Signed-out visitors to the internal homepage see how to sign in or register. Internal Documents offers user registration at `/register` and administrator applications at `/register?audience=admin`. Both start enrollment on the fixed HTTPS `DOCS_ACCOUNT_ORIGIN` (defaults to `https://account.onesa.eu` for the production Auth0 issuer, otherwise to the configured issuer). Configure Identity’s registration return for `internal-documents` on the web as `https://docs.internal.onesa.eu/registration/return`. A browser-bound, one-use bookmark resumes the original document through the Docs client’s own authorization flow. Document access continues to require a product grant and the document’s ACL.

The sign-in flow uses Authorization Code with S256 PKCE, nonce and one-use browser-bound state. Signed access tokens must match the configured issuer and resource. The Onesa UUID and scoped products/roles are namespaced claims. The canonical grant is `internal-documents`. Previously approved `engineering-documents` identities remain eligible **only for the engineering collection** and still require per-document ACLs. Search, metadata, preview, drafts and attachment downloads apply the same restriction. HR access requires an explicit ACL plus canonical Internal Documents access. Portal and Identity administrators inherit no document grants.

## Deploy Internal Documents on the Oracle host

Use the existing host's Docker Engine, Compose v2 and host-network Cloudflare Tunnel. Set these deployment values on the Oracle host:

| Variable | Value |
| --- | --- |
| `DOCS_HOST_PORT` | Port reserved for Documents on host loopback. Check existing listeners and container port mappings before assigning it. |
| `DOCS_VOLUME` | Absolute path on the encrypted private volume containing the restored catalog or reviewed import described below. The directory and database must be writable by container UID 10001. Compose requires an existing directory. |
| `DOCS_CLIENT_ID` | Production Onesa Auth0 confidential Docs client ID, registered with callback `https://docs.internal.onesa.eu/callback` and the Docs API audience. |
| `DOCS_CLIENT_SECRET_PATH` | Absolute path to that client's secret file, outside the checkout. Standalone Compose bind-mounts file-backed secrets. Restrict the file to UID 10001 with mode 0400, or use the equivalent host-mapped UID with a remapped Docker engine. |

From the checked-out release revision, build the image on the Oracle host. The Dockerfile, dependencies and application source come from the same commit.

```sh
set -eu
revision=$(git rev-parse HEAD)
export DOCS_IMAGE="onesa-internal-documents:${revision}"
git archive "$revision" Dockerfile requirements.txt internal_documents |
  docker build --label "org.opencontainers.image.revision=$revision" -t "$DOCS_IMAGE" -
```

For a first import, run the private migration procedure below on the Oracle volume. Once the volume contains the catalog, start the service in the same shell. The image uses the database readiness probe on port 8000.

```sh
docker compose -f deploy/compose.oracle.yaml up -d --wait
curl -fsS "http://127.0.0.1:$DOCS_HOST_PORT/readyz"
```

### Cloudflare edge for the internal hostname

Deploy `deploy/cloudflare/internal-documents.mjs` as a module Worker in the Onesa account. Under **Settings → Domains & Routes**, attach the exact Custom Domain `docs.internal.onesa.eu`. Cloudflare creates the DNS record and certificate for this nested hostname. Disable the `workers.dev` and preview URLs. Keep request URLs and callback query strings out of Worker logs and traces.

Until the Oracle service is ready, leave `DOCUMENTS_SERVICE` unbound. The Worker returns 503 on the canonical hostname without reading document data. This allows verification of DNS and the edge certificate before cutover.

After confirming the private host's readiness, register a single HTTP VPC Service against the existing host-network Oracle production tunnel. Select its loopback IPv4 address `127.0.0.1` and the reserved HTTP port `DOCS_HOST_PORT` from the running Compose service. Under the Worker's **Bindings**, bind that VPC Service with the exact name `DOCUMENTS_SERVICE`. This hostname's DNS belongs to the Worker Custom Domain. The VPC binding reaches Oracle without a separate public tunnel hostname. See [Cloudflare Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/) and [VPC Services](https://developers.cloudflare.com/workers-vpc/get-started/).

Verify anonymous `/api/documents` returns 401, a protected bookmark redirects through Onesa sign-in, and a controlled reader sees only authorized documents after returning. Recheck downloaded bytes and retained bookmark paths against the private source. Removing the binding returns the edge to the 503 maintenance response while preserving the domain, certificate and stored documents. Retain the previous image and private backup for rollback.

## Inventory, import and recovery

`inventory/engineering-material.csv` identifies 553 Git-tracked document candidates from nine Onesa repositories by full commit, path and blob hash. It grants neither editorial approval nor reader access. Reconcile protected engineering material, HR, operations, other departmental sources, effective ACLs, cloud storage, aliases and approved versions separately with their owners. Preserve source authorship, stable document/revision/attachment IDs, bookmarks and tenant ACLs. Do not put any private content into this repository, including the HR index at record level.

Import an owner-reviewed manifest and objects with `python -m internal_documents DATABASE MANIFEST --sha256 EXPECTED_HASH`. Supply original IDs, document `collection`, product and discipline, original author, bookmark, revisions, approvals, original attachment IDs and digests and reviewed user ACLs. Store attachment bytes alongside the manifest as `objects/<sha256>`. The importer operates transactionally, checks independent counts and file hashes, and rejects conflicting existing records. Keep the manifest and `objects/` directory inside the private `DOCS_DATA` volume and outside the public source repository; the CLI rejects manifests outside that volume and secures its directory and SQLite file. The root `.gitignore` and `.dockerignore` exclude common import-data paths. Legacy schema-1 engineering exports without a collection must include the explicit top-level `legacy_engineering: true` attestation and may contain only engineering records. Every new import must set an explicit `collection` on every document; HR and other non-engineering records cannot use the legacy fallback. Replaying the same manifest leaves subsequent local drafts intact. Git provenance links identify immutable source revisions. Each grant supports exactly `user_id` and `permission` (`read` or `edit`). Preserve the source owner’s conditions, including expiration, before importing any unsupported grant. If a previous importer recorded the same manifest, reconcile its existing grants against the source. Other unsupported record types also require explicit preservation.

Access tokens expire as issued. Cache recognized issuer signing keys for up to 24 hours since a successful fetch, with normal refresh every five minutes. An API restart can use those keys. A missing key or expired access token denies access. On expiry a new authorization flow restores the original bookmark. An editor with unsaved form content can sign in in a second tab and submit again. Removing an object ACL affects the next request. Identity changes affect future issuance and any existing self-contained access at its bounded expiry.

Before cutover, restore the private volume to a separate environment and reconcile original and post-backup records, revisions, drafts, ACLs, and attachment hashes. Qualify current-reader/editor/admin workflows, restricted search/downloads, public unsigned bookmarks, and migrated bookmark paths on the internal hostname with real controlled accounts in staging, then repeat applicable production checks. Container smoke tests and local synthetic fixtures establish implementation behavior only.
