# Paladin GIS 0.3.1 — validation plan

Run the gates **in order**. A failure at gate N invalidates everything after it, so stop and fix before going on. Record results in the sign-off table at the end.

```
QGIS plugin ─┐                                   ┌─> WxPal My GIS (reads S3 directly)
             ├─> mcleod (gis.paladinindustries.com) ─> S3 paladin-user-disturbance ─┤
ArcGIS Pro ──┘     auth · stamps author/org · 409 · visibility                    └─> simulation fireBreaks
```

## Gate 0 — Infrastructure

Pass condition: ACM cert `VALIDATED`, the `mcleod-prod` 443 listener is live, Cloudflare has `gis` proxied with Full (strict) for that hostname, and the bot-protection skip rule is in place.

Checked by validator T0.1–T0.3 (next gate).

## Gate 1 — mcleod contract (automated)

```bash
python3 scripts/validate_pipeline.py
```

Use **three accounts**:

| Account | Who | Requirement |
|---|---|---|
| A | your GIS-licensed account | required |
| B | a user in a **different** org | needed for the visibility and authorization tests |
| C | a second user in **A's** org | needed for the org-sharing test |

Make throwaway Cognito users for B and C if you don't have real ones.

Pass condition: **0 FAIL**. Each WARN needs a written decision. These tests are release-blocking if they fail:

| Test | Why it blocks release |
|---|---|
| T0.3 | Customers still on QGIS 0.3.0 send Python's default user agent. If Cloudflare blocks it, they're locked out. |
| T2.4 / T2.5 | If the server trusts the client's author/org, anyone can post as anyone and visibility is meaningless. |
| T2.6 | Without a 409 on duplicate version numbers, concurrent edits fork a tactic's history silently, and the client fixes in 0.3.1 can't work. |
| T3.1 / T3.4 | A private or org tactic readable by another org is a customer-data leak. |
| T3.3 | If B can write a version into A's tactic, anyone can overwrite or delete anyone's plan. |

## Gate 2 — Each client alone

Install from the **pre-release assets** (`v0.3.1-rc*`), never a dev checkout. Use a clean profile each time.

**Clean state:**
- **Pro:** delete `%APPDATA%\PaladinGIS` and the project's `Paladin.gdb`.
- **QGIS:** Settings → Options → Advanced, filter `paladin_gis`, delete the group.

**Run each item below in both clients:**

| # | Step | Expected |
|---|---|---|
| 2.1 | Sign in with a wrong password | Friendly error |
| 2.2 | Sign in with an account not licensed for GIS | "Contact sales" message |
| 2.3 | Sign in with a real account | Signed in. Pro: layers appear. QGIS: identity is filled in. |
| 2.4 | Draw a dozer line, handline, prescribed burn, a fuel break drawn as a line, and (Pro only) a line drawn with the Arc tool | All five stored |
| 2.5 | Sync | 5 uploaded |
| 2.6 | Sync again | 0 uploaded (nothing is re-sent) |
| 2.7 | Move one vertex, then Sync | 1 edited, v2 |
| 2.8 | Change visibility to org, then Sync | 1 edited, v3 |
| 2.9 | Delete one tactic, then Sync | 1 tombstone, and it's gone locally |
| 2.10 | Import a KMZ containing a polygon, a line and a point, all as "Dozer Line" | Polygon in Areas, line in Lines, point skipped with a warning |
| 2.11 | Turn wifi off, edit two tactics, Sync, turn wifi on, Sync | First sync fails; the second uploads both edits |
| 2.12 | Pro only: Sync with unsaved edits | "Save your edits" message, not a lock traceback |
| 2.13 | Restart the app, then Sync | Still signed in; 0 changes; nothing duplicated |

## Gate 3 — Cross-client (the core of this release)

Use the same account in both clients unless noted.

| # | Writer | Action | Reader | Expected in reader after Sync |
|---|---|---|---|---|
| 3.1 | QGIS | create a line and a polygon (org) | Pro | Both appear, in the correct layers, with the correct template, width and name |
| 3.2 | Pro | create a line and a polygon (org) | QGIS | Both appear and are editable |
| 3.3 | Pro | edit a QGIS-made tactic | QGIS | Updated; version incremented by 1; supersedes points to the previous version |
| 3.4 | QGIS | delete a Pro-made tactic | Pro | Removed |
| 3.5 | **both** | edit the **same** tactic offline in each, then sync QGIS first, then Pro | both | Versions run 1, 2, 3 with no duplicate number; Pro's edit wins and both end up identical (local edit wins, linear history) |
| 3.6 | A (Pro) | private tactic | B (QGIS, other org) | Not visible |
| 3.7 | A (Pro) | org tactic | C (QGIS, same org) | Visible |

To check 3.5 at the source of truth, look at the S3 versions themselves:

```bash
aws s3 ls s3://paladin-user-disturbance/disturbances/<tactic_id>/
```

Every `000NNN-` prefix should appear exactly once.

## Gate 4 — Downstream (WxPal + simulator)

| # | Check | Expected |
|---|---|---|
| 4.1 | `validate_pipeline.py --leave-public`, then open WxPal → My GIS → search "PALADIN-VALIDATION" | Black dozer line with X marks near Alum Rock |
| 4.2 | A **private** tactic from Pro while signed into WxPal as the same user | **Visible.** If it isn't, WxPal's identity doesn't match the author mcleod stamps (see note below) |
| 4.3 | The tombstoned tactic from 2.9 | **Not** shown in WxPal |
| 4.4 | The tactic edited in 2.7 | WxPal shows v2's geometry, not v1's |
| 4.5 | Include the dozer line (open, across the fire path) in a run; compare with it excluded | Fire is held or slowed at the line only when included |
| 4.6 | Include the Rx burn polygon | Its interior doesn't burn |
| 4.7 | A fuel break drawn as a line (2.4) | Renders and simulates as a buffered line, not a closed polygon |
| 4.8 | `validate_pipeline.py --cleanup <id>` | Gone from WxPal after a reload |

**Known risk for 4.2.** mcleod stamps `author` with the Cognito sub. WxPal's visibility filter matches it against `identityStore`: either a manually entered string (originally the email) or `userDetails.id`. If `userDetails.id` isn't the Cognito sub, every private tactic disappears in WxPal for its own author. Fix this in WxPal (`identityStore.ts` is the single place to change). Don't change it in the plugins.

## Gate 5 — Release artifacts

Download the assets from the rc pre-release page and check:

- [ ] Pro toolbox description shows `v0.3.1-rc*`.
- [ ] QGIS plugin manager shows 0.3.1.
- [ ] `releases/latest/download/PaladinGIS.pyt` still serves the **previous stable** release (proves RCs aren't reaching users).

## Sign-off

| Gate | Result | Date | Tester | Notes / WARN decisions |
|---|---|---|---|---|
| 0 Infra | | | | |
| 1 mcleod contract | | | | attach `pipeline_validation_*.json` |
| 2 QGIS solo | | | | |
| 2 Pro solo | | | | |
| 3 Cross-client | | | | |
| 4 WxPal + sim | | | | |
| 5 Artifacts | | | | |

Tag `v0.3.1` only when every row passes.
