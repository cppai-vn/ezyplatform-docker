# EzyPlatform Docker

Java 11 runtime with the **latest EzyPlatform** (1.5.4 at the time of writing) and **EzyArticle preinstalled and activated** (1.4.9 at the time of writing), so a fresh volume opens straight into an admin that can manage pages, posts and page fragments. Database credentials must be supplied at runtime; do not build or commit secrets into the image.

```sh
docker build --platform linux/amd64 -t vutadev/ezyplatform-app:latest .
```

Without build args the image downloads the latest platform and the latest EzyArticle compatible with it from the official marketplace. Pin both for a reproducible release:

```sh
docker --context orbstack build --platform linux/amd64 \
  --build-arg EZ_VERSION=1.5.4 --build-arg EZYARTICLE_VERSION=1.4.9 \
  -t vutadev/ezyplatform-app:1.5.4 .
docker --context orbstack push vutadev/ezyplatform-app:1.5.4
```

| Build arg | Default | Meaning |
|---|---|---|
| `EZ_VERSION` | `latest` | EzyPlatform core version |
| `EZYARTICLE_VERSION` | `latest` | EzyArticle version, downloaded for `EZ_VERSION` |

`preinstall-modules.py` runs only in the build stage: it validates every ZIP entry before writing (no traversal, no symlinks, no files outside the module's own `admin|web|socket` folders), extracts the module and marks it active in `.runtime/<lane>/<container>.txt`, the same files `cli.sh "activate ..."` writes.

The compose image references are pinned to `1.5.4`; publishing this tag does not deploy it or change `latest`.

### Existing volumes are not upgraded

Docker copies the image's `/app/ezyplatform` into a named volume only when the volume is empty. A volume created from an older image keeps its old core and has no EzyArticle. Back it up first. For a local site, the simplest path is a fresh volume (`docker compose down -v`, which deletes the data). To only add EzyArticle to an existing volume, use the official CLI inside the running container:

```sh
docker compose exec ezyplatform bash cli.sh "install ezyarticle"
for type in admin-plugin web-plugin socket-plugin; do
  docker compose exec ezyplatform bash cli.sh "activate $type ezyarticle"
done
docker compose restart ezyplatform
```

Upgrading the core of an existing volume is a migration: follow "Before a production rollout" below.

Upgraded plugins/custom content of a restored production volume are **not bundled into this base image**.

## Startup and health

`entrypoint.sh` configures `settings/setup.properties`, starts admin and optionally web through the platform CLI, and retains JVM stdout/stderr in `logs/admin-console.log` / `logs/web-console.log`. Application logs remain in the existing `*-server.log` files. The container streams both.

Startup succeeds only when enabled services return HTTP 2xx/3xx. A Java PID alone is not considered healthy: a failed main thread can leave non-daemon workers running. After repeated HTTP failures the entrypoint exits nonzero, allowing Docker/Swarm to recover. No per-container cron restart loop is installed.

| Variable | Default |
|---|---|
| `AUTO_START_WEB` | `false` |
| `STARTUP_TIMEOUT_SECONDS` | `180` |
| `HEALTHCHECK_INTERVAL_SECONDS` | `30` |
| `HEALTHCHECK_FAILURE_THRESHOLD` | `3` |
| `ADMIN_HEALTHCHECK_URL` | `http://127.0.0.1:9090/` |
| `WEB_HEALTHCHECK_URL` | `http://127.0.0.1:8080/` |

Choose suitable health URLs if the root route redirects off-site or does not return 2xx/3xx. The image also defines a Docker healthcheck. The maintained Temurin Java 11 base avoids retired Debian 11 security-package downloads; Python is used only in the platform extraction build stage, not runtime.

## Isolated local reproduction

**Do not run a restored app against the production database.** `.local-repro/` contains private restored data; it and `.artifacts/` are excluded from Git and Docker build context.

1. Verify the volume backup's `SHA256SUMS` and extract its app archive into `.local-repro/app/`.
2. Replace only the restored root `settings/setup.properties` connection settings with:

   ```properties
   datasource.jdbc_url=jdbc:mysql://mysql:3306/blog
   datasource.username=ezyplatform
   datasource.password=local-repro-only
   tables.create_manually=false
   ```

3. Build and start the local MySQL service:

   ```sh
   docker --context orbstack build --build-arg EZ_VERSION=1.5.0 -t ezyplatform-local-fixed:1.5.0 .
   docker --context orbstack compose -p ezy-local-repro -f compose.local-repro.yml up -d mysql
   ```

4. Import a separately approved database snapshot into **local MySQL only**, with local app processes stopped. The volume backup does not contain the external database. An empty database can reproduce dependency conflicts, but not the production content. Example:

   ```sh
   set -o pipefail
   gzip -dc .artifacts/backups/<db-backup>/blog.sql.gz | \
     docker --context orbstack compose -p ezy-local-repro -f compose.local-repro.yml \
       exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot blog'
   ```

   Use a new local database or replace only this project's local schema before import. Never import a backup into production as part of reproduction.

5. With local app processes still stopped, quarantine duplicate core-provided dependencies and upgrade the pinned plugin set:

   ```sh
   python3 scripts/quarantine-core-dependencies.py --root .local-repro/app
   python3 scripts/quarantine-core-dependencies.py --root .local-repro/app \
     --quarantine .local-repro/quarantine-before-upgrade --apply
   python3 scripts/upgrade-local-modules.py --runtime .local-repro/app \
     --rollback .local-repro/plugins-before-upgrade --cache .local-repro/vendor
   # Inspect/apply another quarantine pass if new modules bundle core providers.
   python3 scripts/quarantine-core-dependencies.py --root .local-repro/app
   ```

   `plugin-lock.json` pins versions and ZIP SHA256 from the official marketplace. The installer checks the local-only JDBC URL, core version, archive hashes, extraction paths and module versions before replacing modules; old directories and relevant runtime caches are retained for rollback. New dependency plugins are activated in the local runtime cache. This is a **local migration tool, not a production deployment tool**.

6. Start the app and preview proxy:

   ```sh
   docker --context orbstack compose -p ezy-local-repro -f compose.local-repro.yml up -d
   ```

   - Website: <http://127.0.0.1:18080/>
   - Admin: <http://127.0.0.1:19090/login>

The app and MySQL join only an `internal: true` network, with no production network or outbound route. Only the reverse proxy joins a separate preview network and binds host ports to loopback. **Outbound email, payments, banking, chat and webhook integrations are intentionally not exercised.** Restored content and credentials remain sensitive even when network-isolated.

`scripts/local-repro-start.sh admin|web` is a console-mode diagnostic runner. It preserves stderr and refuses a non-local JDBC URL. Use it with a separate restored copy and `docker compose run --entrypoint bash app /local-repro-start.sh admin` if reproducing the original unmodified dependency set; never run a second writer against the active runtime volume.

## What caused the October 2026 outage

The restored `ladingpage` plugin/theme bundled Thymeleaf Layout Dialect **3.1.0**, while core 1.5.0 supplied **3.4.0**. Groovy rejected the conflicting extension module versions and terminated main; worker threads kept the JVM alive. Old launch scripts redirected the exception to `/dev/null`.

Separately, ecommerce 0.9.8 referenced the old return type of `saveAdminRoleNameIfNotExists`, causing its role configuration to be skipped. The pinned compatible plugin upgrade addresses that warning; removing duplicate dependencies alone does not fix plugin/core API incompatibility.

## Tests

```sh
python3 -m unittest discover -s tests -v
# Also run Linux entrypoint fixtures in the built image:
RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v
bash -n entrypoint.sh scripts/local-repro-start.sh
```

## Before a production rollout

- Back up both persistent volumes **and MySQL**, then rehearse migrations on the approved DB snapshot.
- Upgrade the persistent plugin directories and clean bundled core providers deliberately. **Building/replacing an image does not upgrade an existing `/app/ezyplatform` volume.**
- Preserve custom `ladingpage` code/content; validate its complete feature surface after changing core APIs.
- Use `stop-first` updates and a single writer for this shared application volume; do not overlap tasks with `start-first`. Resolve any still-running superseded container under a controlled maintenance plan.
- Validate authenticated business flows and external integrations in staging. Local startup/public-page checks do not certify payments, banking or shipping.

No production deployment is performed by the local reproduction scripts.

### Controlled PayFS volume recovery

`scripts/apply-production-recovery.py` is a separate, explicit production filesystem patch tool. It accepts only the fixed PayFS volume/service, requires replicas=0 and no running volume users, validates all before/after module/JAR/cache hashes, preserves core/settings, and keeps a rollback journal outside the runtime. It never changes JDBC settings or runs SQL. Prepare and review the manifest/payload from a fresh quiesced backup; dry-run must pass before `--apply`. Do not point the local-only module installer at production or bypass its JDBC guard.

The October 2026 recovery upgraded 28 official module directories and quarantined 143 duplicate JARs, then started a single digest-pinned 1.5.0 task. `compose.payfs-landing.yml` now matches the recovered policy: stop-first update/rollback, one replica/max-per-node, 180s update monitor, 300s restart window, 45s stop grace. The PayFS-only raw compose stored in Dokploy and its generated source were also backed up and synchronized, preserving environment, ports, networks, volumes and routing labels. Do not reintroduce start-first for the shared volume.

A filesystem rollback alone does not roll back database migrations. Keep the independently verified MySQL snapshot and rehearse schema compatibility before reverting plugin versions. Private recovery artifacts are under `.artifacts/backups/20261005-124412-cpp-prod-payfs-recovery/`; never publish these backups.
