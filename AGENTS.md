# Onesa Engineering Documents

This repository owns the protected product, including the web client, document API, ACLs, revisions, attachment storage and its release. Preserve the public Developer Docs edition, existing engineering bookmarks, document identities, source approval and author provenance. Keep actual identity and content snapshots, attachments, session databases and signing credentials out of Git. A Git file inventoried as a candidate has no implied approval or access rights.

Run `python -m unittest discover -s tests -v` before promotion. Distinguish an isolated fixture, a privately hosted staging pilot and a production release in evidence. Follow the migration and release procedure in README.md. A change to the web client, OIDC configuration or audience must be exercised through a signed-out document bookmark with a product-scoped user and an ordinary user. Maintain the existing production hosts until their replacements pass.
