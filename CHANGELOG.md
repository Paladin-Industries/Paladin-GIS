# Changelog

All notable changes to this plugin are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [0.1.1] - Unreleased

### Fixed

- Tactic storage is now scoped per organization: the S3 key prefix is derived
  from the Org ID (`disturbances/orgs/<org_id>/…`) instead of a separate
  setting. Credentials are issued scoped to an organization's own prefix, so
  the previous shared default produced an S3 403 on first sync with no way to
  correct it from the plugin UI. Applies to both the QGIS plugin and the
  ArcGIS Pro toolbox, which derive identical prefixes.

## [0.1.0] - Unreleased

Initial public release.

- LANDFIRE 2025 CONUS fuel and canopy image services
- Draw and import tactics: fuel breaks, prescribed burns, dozer lines,
  handlines, scratch lines
- Append-only versioned sync to the Paladin tactic store, with
  public/org/private visibility and conflict protection for local edits
