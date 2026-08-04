#!/usr/bin/env bash
# Build the installable packages.
#
#   dist/paladin_gis-<version>.zip        QGIS plugin  (folder at zip root —
#                                         QGIS's Install from ZIP requires it)
#   dist/paladin_arcgis_pro-<version>.zip ArcGIS Pro toolbox, if present
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="$(grep -E '^version=' paladin_gis/metadata.txt | cut -d= -f2 | tr -d '[:space:]')"

rm -rf dist && mkdir -p dist
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

zip -rq "dist/paladin_gis-${VERSION}.zip" paladin_gis \
  -x '*__pycache__*' -x '*.pyc' -x '*.DS_Store'
echo "Built dist/paladin_gis-${VERSION}.zip"

if [ -f arcgis_pro/PaladinGIS.pyt ]; then
  # Pro reads the .pyt from disk; the zip is just a delivery wrapper, so its
  # contents sit at the archive root rather than inside a folder.
  ( cd arcgis_pro && zip -rq "../dist/paladin_arcgis_pro-${VERSION}.zip" . \
      -x '*__pycache__*' -x '*.pyc' -x '*.DS_Store' -x 'PLACEHOLDER.md' )
  echo "Built dist/paladin_arcgis_pro-${VERSION}.zip"
else
  echo "Skipping ArcGIS Pro package (arcgis_pro/PaladinGIS.pyt not present)"
fi
