# Paladin GIS — Access Credentials

**Confidential.** This sheet contains credentials tied to your subscription.
Store it in a password manager, not in email, chat, or a shared drive. If it is
exposed, tell us and we will rotate the keys — rotation does not affect any
tactics already synced.

Send this sheet through a secret-sharing link or a password manager share, not
as an email attachment. The startup guide can go by ordinary email; this cannot.

---

## Your details

| Field | Value |
|---|---|
| Organization | `{{ORG_NAME}}` |
| **Organization ID** | `{{ORG_ID}}` |
| Key prefix | `{{PREFIX}}` |
| Bucket | `{{BUCKET}}` |
| Region | `{{REGION}}` |
| Issued | `{{ISSUE_DATE}}` |
| Tier | Intel |

## Credentials

| Field | Value |
|---|---|
| **Access Key ID** | `{{ACCESS_KEY_ID}}` |
| **Secret Access Key** | `{{SECRET_ACCESS_KEY}}` |
| Session token | `{{SESSION_TOKEN_OR_BLANK}}` |

These are issued to your organization. Each person also enters their **own
email address** as Author — see below.

---

## Entering them

### QGIS

1. Open the Paladin GIS panel, expand **Settings**.
2. Fill in:

   | Setting | What to enter |
   |---|---|
   | Author | **the individual user's Paladin account email** |
   | Organization ID | `{{ORG_ID}}` — the same for everyone at your agency |
   | Access Key ID | from the table above |
   | Secret Access Key | from the table above |
   | Bucket / Region / Prefix | as listed above |
   | Default visibility | `org` unless you have a reason to change it |

3. Save. Settings are stored in the QGIS user profile, so each workstation is
   configured once and the values are not carried in shared project files.

### ArcGIS Pro

1. Run **1) Configure Paladin Settings**.
2. Enter Author, Organization ID and default visibility.
3. **Expand the "Credentials" section** — Pro renders it collapsed, and this
   is the single most common setup mistake. Enter the access key and secret.
4. Advanced holds bucket, region and prefix; they match the table above.
5. Run the tool. Settings are written to
   `%APPDATA%\PaladinGIS\settings.json`.

---

## Author vs. Organization — what controls visibility

These are two different things and both matter.

**Organization ID** is shared. Everyone at your agency enters the same
`{{ORG_ID}}`. It is what makes a tactic marked `org` visible to your whole
team. If someone mistypes it, they will sync successfully and then quietly see
none of their colleagues' work — that symptom almost always means a typo here.

**Author** is per person: your own Paladin account email. It stamps each
version you create, so the history shows who did what, and it is what `private`
tactics match on. Do not use a shared mailbox — if two people use the same
author address, their private tactics become visible to each other.

Together they give three levels:

| Visibility | Who sees it | Typical use |
|---|---|---|
| `public` | every Paladin user | rarely — genuinely open information |
| `org` | anyone with `{{ORG_ID}}` | **the normal choice** — your agency's work |
| `private` | only that author | drafts and personal scratch work |

Visibility is chosen per tactic when it is drawn, and the default for new
tactics is `org`.

---

## Verifying it works

1. Enter the settings and sync once. A first sync on an empty store completes
   with nothing uploaded and nothing downloaded — that is success, not a
   failure.
2. Draw one test tactic, sync, and confirm it uploads.
3. Have a colleague on another workstation sync and confirm they see it. That
   proves the organization ID matches on both machines.
4. Delete the test tactic (set `status` to `inactive`, sync) and confirm it
   disappears on their next sync too.

If step 3 fails but steps 1 and 2 worked, compare the organization ID on both
machines character by character.

---

## Rotation and offboarding

Keys can be rotated on request, and should be when someone with access leaves.
Rotation issues a new key pair; tactics already synced are unaffected, and each
workstation re-enters the new keys once.

Because the store is append-only, removing a person's access does not remove
their tactics — their work stays with the organization, attributed to them.

---

## Support

Technical problems with the tools:
<https://github.com/Paladin-Industries/Paladin-GIS/issues>
(never post keys, emails, or your organization ID)

Credentials, access, and subscription: **{{SUPPORT_EMAIL}}**
