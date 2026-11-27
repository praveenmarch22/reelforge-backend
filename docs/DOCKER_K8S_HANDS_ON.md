# Docker and Kubernetes, hands-on with ReelForge

A plan for learning by doing, on the project you already know. Each exercise says what to type, what to look for, and the question an interviewer would ask about it. Do them in order; type the commands yourself rather than pasting.

**Before you start: free some disk.** Docker needs room to build images, and the C: drive was nearly full on 2026-11-28. Aim for 20 GB free (the list of large files is in the session notes: the two Downloads files, the npm cache, the unused Ollama model).

All commands run in PowerShell from `C:\Users\prave\Postman\reelforge\backend` unless it says otherwise.

## Part 1: Docker (about 3 hours)

### 1. See what is running
```powershell
docker compose ps
```
```powershell
docker stats --no-stream
```
- Find the 9 services. Which use the most memory? Why is `renderer` the largest image (`docker images`)?
- **Interview:** *What is the difference between an image and a container?* (An image is the read-only template; a container is a running instance with its own writable layer.)

### 2. Read a Dockerfile line by line
Open `api/Dockerfile` and `renderer/Dockerfile`. For each line, say what it does and why it is in that order.
- Why are the `requirements` files copied **before** the source code? (Layer caching: dependencies are reinstalled only when they change.)
- Why does the image end with `USER app`? (Not running as root limits the damage of a break-in.)
- Why is the renderer's base image pinned with `@sha256:…`?

Prove the cache: change a comment in a `.py` file and rebuild, then change `requirements.txt` and rebuild. Compare the times.
```powershell
docker compose build api
```

### 3. Go inside a container
```powershell
docker compose exec api sh
```
Inside: `whoami`, `ls`, `env | grep S3`, `python -c "import flask; print(flask.__version__)"`, `exit`.
- Notice the hostname `postgres` in `DATABASE_URL`. **Interview:** *How do containers find each other?* (Compose puts them on one network with DNS names equal to the service names.)

### 4. Logs, and breaking something on purpose
```powershell
docker compose logs -f worker
```
In a second terminal, stop Redis, watch what the worker and the API health check do, then start it again:
```powershell
docker compose stop redis
```
```powershell
curl.exe http://localhost:5000/api/v1/health
```
```powershell
docker compose start redis
```
- **Interview:** *What is a health check for?* (So the orchestrator knows when a container is ready, or broken, instead of just "started".)

### 5. Volumes: where the data lives
```powershell
docker volume ls
```
```powershell
docker compose down
```
```powershell
docker compose up -d
```
Your projects are still there. Now read (don't run) what `docker compose down -v` would do.
- **Interview:** *What happens to data when a container is deleted?* (The writable layer is lost; volumes survive.)

### 6. Write a Dockerfile yourself: the frontend
The web app has no image yet. In `frontend/`, write a **multi-stage** Dockerfile:
1. stage `build`: `node:22-alpine`, `npm ci`, `npm run build`;
2. stage `serve`: `nginx:alpine`, copy `dist/` in, and an `nginx.conf` that serves `index.html` for every path (a single-page app) and passes `/api/` to `http://api:5000`.

Build and run it, and open http://localhost:8080.
- Compare the final image size with the `build` stage's. **Interview:** *Why multi-stage builds?* (The compilers and `node_modules` stay out of the final image: smaller, faster, safer.)

### 7. Add it to Compose
Add a `web` service to `docker-compose.yml` that uses your image and depends on `api` being healthy. `docker compose up -d web`.

### 8. Environment and secrets
- Find where `GEMINI_API_KEY` travels: `.env` → `docker-compose.yml` → the container's environment → `config.py`.
- Run `docker compose config` and see the file with every variable filled in. (Careful: it prints secrets.)
- **Interview:** *Why not bake secrets into the image?* (Anyone with the image has them, and they'd be in the layer history forever.)

### 9. Scale a service
```powershell
docker compose up -d --scale worker=2
```
Generate a Reel and watch both workers take jobs in their logs.
- Why can the API and the workers scale like this, but not Postgres? (They are stateless.)
- Why must only **one** process run the scheduler (`-B`)? (Two would schedule every task twice.)

### 10. Look inside an image
```powershell
docker history backend-api
```
```powershell
docker image inspect backend-renderer --format '{{.Size}}'
```

## Part 2: Kubernetes (about 4 hours)

Use the cluster built into Docker Desktop: Settings → Kubernetes → Enable. Then:
```powershell
kubectl get nodes
```

The same app, described the Kubernetes way:

| Compose | Kubernetes | Why |
|---|---|---|
| a service with `image:` | **Deployment** (keeps N copies running) + **Pod** | Restarts and rolling updates |
| the service name as a hostname | **Service** | A stable name and address in front of pods that come and go |
| `environment:` | **ConfigMap** (plain settings) and **Secret** (keys) | Settings separate from the image |
| `ports:` on the host | **Service** type NodePort / **Ingress** | Getting traffic in |
| `volumes:` | **PersistentVolumeClaim** | Storage that outlives a pod |
| `healthcheck:` | **readiness** and **liveness probes** | Don't send traffic until ready; restart when stuck |
| `command: flask db upgrade` on start | **Job** | Run once to completion |
| the `-B` scheduler | one-replica Deployment, or **CronJob**s | Exactly one scheduler |
| `--scale worker=2` | `replicas: 2`, or an **autoscaler** | Scaling by a rule |

### 11. First pod and service: Redis
Write `k8s/redis.yaml` with a Deployment (image `redis:7-alpine`, 1 replica) and a Service on port 6379.
```powershell
kubectl apply -f k8s/redis.yaml
```
```powershell
kubectl get pods,svc
```
Delete the pod and watch a new one appear:
```powershell
kubectl delete pod -l app=redis
```
- **Interview:** *What is the difference between a Pod, a Deployment and a Service?*

### 12. Postgres with storage
A Deployment (or StatefulSet) for `postgres:16`, a PersistentVolumeClaim for its data, a Secret for the password, and a Service. Delete the pod and check that the data survives.
- **Interview:** *Why a StatefulSet for databases?* (Stable names and one volume per replica.)

### 13. ConfigMap and Secret for the API
Put the non-secret settings (`ENV`, `S3_BUCKET`, `WEB_ORIGIN`, `LLM_PROVIDERS=mock`, …) in a ConfigMap and the secret ones in a Secret. Use the mock providers first, so nothing depends on real keys.

### 14. The API Deployment
- Image: the one you built (`backend-api`; Docker Desktop's cluster can use local images with `imagePullPolicy: Never`).
- `envFrom` the ConfigMap and the Secret.
- A **readiness probe** on `/api/v1/health`.
- 2 replicas, and a Service.
```powershell
kubectl port-forward svc/api 5000:5000
```
```powershell
curl.exe http://localhost:5000/api/v1/health
```

### 15. Migrations as a Job
A Job that runs `flask db upgrade` once. Watch it complete: `kubectl get jobs`, `kubectl logs job/migrate`.
- **Interview:** *Why not run migrations in every API pod's start command?* (Two pods starting together would both try.)

### 16. The workers
Three Deployments from the same image with different commands (queues `default,llm,voice,visuals,maintenance` with `-B`; `render`; `publish`). Scale one:
```powershell
kubectl scale deployment worker --replicas=3
```
- The `-B` worker must stay at 1 replica. How would you split the scheduler out so the others can scale?

### 17. A rolling update, and a rollback
Change something visible (a log line), rebuild the image with a new tag, set the Deployment to it, and watch:
```powershell
kubectl rollout status deployment/api
```
```powershell
kubectl rollout undo deployment/api
```
- **Interview:** *How does Kubernetes update without downtime?* (New pods start and pass readiness before old ones are stopped.)

### 18. Break it and debug it
Set a wrong image name, or remove a required setting. Practise the four commands you'll use most:
```powershell
kubectl get pods
```
```powershell
kubectl describe pod <name>
```
```powershell
kubectl logs <name> --previous
```
```powershell
kubectl get events --sort-by=.lastTimestamp
```
Learn to recognise `ImagePullBackOff`, `CrashLoopBackOff` and `Pending`.

### 19. Resource limits
Give the API `requests` and `limits` for CPU and memory. Set the renderer's memory limit too low on purpose and see `OOMKilled`.
- **Interview:** *Requests versus limits?* (Requests are what the scheduler reserves; limits are the ceiling.)

### 20. Clean up
```powershell
kubectl delete -f k8s/
```

## What to say you did (after you've done it)

- Containerised a 9-service application with Docker Compose: multi-stage builds, health checks, non-root images, pinned base images.
- Deployed it to Kubernetes: Deployments, Services, ConfigMaps, Secrets, a migration Job, persistent storage, readiness probes, rolling updates and rollbacks.

Then deploy for real using [DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md).
