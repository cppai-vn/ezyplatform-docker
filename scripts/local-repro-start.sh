#!/usr/bin/env bash
set -euo pipefail
lane="${1:?expected admin or web}"
case "$lane" in
  admin) heap=256m ;;
  web) heap=512m ;;
  *) echo 'Expected admin or web' >&2; exit 2 ;;
esac
# A restored backup contains production credentials. Refuse to start until sanitized.
if ! grep -qx 'datasource.jdbc_url=jdbc:mysql://mysql:3306/blog' settings/setup.properties; then
  echo 'Refusing reproduction: JDBC URL must point to the isolated local mysql service.' >&2
  exit 1
fi
classpath="lib/*:external/lib/*:external/resources:settings:$lane/lib/*:$lane/settings:$lane/resources"
if [[ "$lane" == web ]]; then
  classpath+=":web/public"
fi
for plugin in "$lane"/plugins/*; do
  [[ -d "$plugin" ]] || continue
  classpath+=":$plugin/lib/*:$plugin/resources"
done
if [[ "$lane" == web ]]; then
  for theme in web/themes/*; do
    [[ -d "$theme" ]] || continue
    classpath+=":$theme/lib/*:$theme/resources"
  done
fi
# Keep stderr/stdout visible: the original start-service scripts discard exceptions.
main="org.youngmonkeys.ezyplatform.$lane.${lane^}Startup"
exec java "-Xmx$heap" -cp "$classpath" "$main"
