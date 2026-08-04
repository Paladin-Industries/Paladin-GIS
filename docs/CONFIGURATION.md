# Configuration

Open the Paladin GIS panel and expand **Settings**.

| Setting | What it is |
|---|---|
| Author | Your Paladin account email. This is what Paladin matches for visibility, so it must be the address on your account. |
| Organization ID | Issued with your credentials. Controls what `org`-visibility tactics you can see. |
| Default visibility | `public`, `org`, or `private` — applied to newly drawn tactics. `org` by default. |
| Access key / Secret key | Issued by Paladin. |
| Session token | Only if you were issued temporary credentials. |
| Bucket / Region / Prefix | Leave at the defaults unless Paladin told you otherwise. |

Settings are stored in your QGIS profile (`QgsSettings`, group
`paladin_gis`), not in the project file — so they follow the workstation, not
the project you share with colleagues.

In ArcGIS Pro the equivalent tool is **1) Configure Paladin Settings**, and the
values are written to `%APPDATA%\PaladinGIS\settings.json`. The credential
fields render under a collapsed **Credentials** category — expand it before
saving, or the keys stay blank.

There is no Paladin API server in this configuration: both clients sign
requests to the object store directly, so the bucket and region defaults are
correct unless Paladin issued you different ones.

## Visibility

| Level | Who sees it |
|---|---|
| `public` | Everyone with plugin access |
| `org` | Anyone whose organization ID matches yours |
| `private` | Only the author |

Visibility is enforced when pulling. Choose deliberately before syncing
anything that shouldn't leave your organization.

## Troubleshooting

**"No AWS credentials"** — the access key and secret are blank. In ArcGIS Pro
this is almost always the collapsed **Credentials** category in tool 1: expand
it, re-enter the keys, and save.

**Tactics sync but don't appear for a colleague** — check the visibility and
that their organization ID matches yours exactly.

**Nothing downloads** — confirm the prefix matches what Paladin issued. A
prefix intended for testing will not see production tactics.
