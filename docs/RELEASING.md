# Releasing

Repository: `git@github.com:Paladin-Industries/Paladin-GIS.git`

1. Update `paladin_gis/metadata.txt` — bump `version=`.
2. Add the changes to `CHANGELOG.md`.
3. Commit, then tag with a matching `v` prefix:

   ```bash
   git tag v0.1.1
   git push origin main --tags
   ```

The release workflow refuses to publish if the tag and `metadata.txt`
disagree, if the plugin fails to byte-compile, or if it finds anything
key-shaped in the source. On success it builds `dist/paladin_gis-<version>.zip`
and attaches it to a GitHub release with generated notes.

Customers install that zip through **Plugins → Install from ZIP**, so the
release asset is the product — check the zip opens to a `paladin_gis/` folder
at its root before announcing it.
