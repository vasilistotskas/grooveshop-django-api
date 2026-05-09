---
name: Infra hardening pass 2026-05-02
description: Seven security/HA fixes applied to grooveshop-infrastructure manifests — readOnlyRootFilesystem, HTTPS redirect, HSTS preload, Redis DB split, static HA, PDB fix, Meilisearch storage class
type: project
---

Seven hardening changes applied to `manifests/app-constructs/grooveshop/base/` on 2026-05-02.

**Why:** Security audit + reliability hardening pass.

**How to apply:** Use as context when reviewing future infra diffs or answering "was X already done" questions.

## Changes made

1. **HTTP→HTTPS redirect** (`ingress-traefik.yaml`): New `grooveshop-ingress-http` Ingress on `web` entrypoint only, applying `redirect-https` Middleware (`redirectScheme: https, permanent: true`). Both `grooveshop-ingress-api` and `grooveshop-ingress-public` stripped to `websecure` entrypoint only. New `redirect-https` Middleware uses `traefik.containo.us/v1alpha1` (Traefik v2 API, consistent with K3s v1.29 which ships Traefik v2).

2. **FLOWER_BASIC_AUTH required** (`backend.yaml`): Removed `optional: true` from the secretKeyRef. Simplified flower command — no longer conditionally applies `--basic_auth`; FLOWER_BASIC_AUTH is now mandatory and always passed. The secret key MUST exist in `backend-secrets` SealedSecret.

3. **readOnlyRootFilesystem: true** on all containers:
   - `backend.yaml`: backend, celery-worker, celery-beat, celery-flower (all 4 containers)
   - `frontend.yaml`: frontend
   - `media-stream.yaml`: media-stream
   - `static.yaml`: static-nginx
   - `backend-prepare-job.yaml`: prepare Job container
   - `reloader/v1.2.0/deployment.yaml`: reloader-reloader (was `securityContext: {}`)
   - celery-beat command got `--schedule=/tmp/celerybeat-schedule` added so the beat schedule DB file goes to emptyDir, not rootfs.

4. **static PDB** (`pdb.yaml`): Changed from `minAvailable: "50%"` (= 0 on 1 replica) to `minAvailable: 1`. Also bumped `static.yaml` replicas from 1→2 (PVCs are RWX so both replicas can mount) and added `topologySpreadConstraints` to distribute across nodes.

5. **Redis DB collision** (`media-stream-config.yaml`): Changed `REDIS_DB` from `"0"` to `"2"`. Django backend uses DB 0 for cache + Celery result backend + Channels. DB 1 reserved/unused. DB 2 is now media-stream's namespace.

6. **HSTS preload** (`ingress-traefik.yaml`): Added `stsPreload: true` to `security-headers` Middleware alongside existing `stsSeconds: 31536000` and `stsIncludeSubdomains: true`.

7. **Meilisearch storage class** (`meilisearch/values.yaml`): Changed from `longhorn-rwx` to `longhorn` (RWO). Single-replica search index doesn't need NFS/RWX; RWO avoids fsync overhead from the NFS layer.
