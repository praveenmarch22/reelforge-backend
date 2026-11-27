# ReelForge — Free Deployment Options

**Checked on 2026-11-28.** Free tiers change often. Every fact below has a source link. Re-check the link before you rely on a number.

Labels used in this document:

- **Verified** — read on the provider's own pricing or docs page on 2026-11-28.
- **Reported** — read on a news site or forum, not on the provider's page.
- **Not verified** — from general knowledge or my own arithmetic. Treat as a guess.

---

## 1. Summary

| Question | Answer |
|---|---|
| Public demo up tomorrow, least friction | **Option C-lite:** one AWS EC2 `m7i-flex.large` running the existing Docker Compose file, paid by the $100–$200 new-account credit. Same x86 images as local. Lasts up to 6 months, then the account closes. |
| Best learning value for Docker and Kubernetes | **Option A:** one Oracle Always Free Arm VM with Docker Compose first, then k3s. Free with no end date, but harder to get and now half the size it used to be. |
| What to do in both cases | Put the frontend on Cloudflare Pages, files on Cloudflare R2, email on Resend or Brevo. Then the VM holds only compute, Postgres and Redis, and is easy to replace. |

Three things changed in 2026 that older guides get wrong:

1. **Oracle halved the free Arm VM.** It is now 2 OCPU and 12 GB, not 4 OCPU and 24 GB.
2. **Hugging Face Docker Spaces are no longer free.** They need a paid plan.
3. **AWS no longer has the 12-month free tier for new accounts.** It is a credit of up to $200 that ends after 6 months.

---

## 2. What the app needs

| Part | Need | Why it is hard to host free |
|---|---|---|
| Frontend (React + Vite) | Static files | Easy. Many free hosts. |
| API (Flask, gunicorn) | ~256–512 MB, always reachable | Easy, but free hosts sleep. |
| Celery workers ×3 + beat | Always running, jobs up to 50 min | Hard. Free hosts allow web services only, and they sleep. |
| Render service (Remotion, Chrome, FFmpeg) | 2 vCPU, 2–4 GB, 1.5–2 GB image | Hardest. No free container host gives this size. |
| PostgreSQL 16 (citext, pgcrypto) | Small | Easy. |
| Redis (Celery broker) | Always on, many small commands | Per-command free tiers do not fit. |
| Object storage | A few GB, S3 API | Easy. |
| Email | Low volume SMTP | Easy. |
| Public HTTPS URL | Fixed URL for Google OAuth | Needs a stable hostname. |

The workers and the render service decide the architecture. Everything else has a good free home.

---

## 3. Compute

| Provider | Free offer | Sleep / expiry | Card needed | Fits ReelForge? | Source |
|---|---|---|---|---|---|
| **Oracle Cloud Always Free (Ampere A1, Arm)** | 1,500 OCPU-hours and 9,000 GB-hours a month. Equal to **2 OCPU and 12 GB**, as one or two VMs. 200 GB block storage. 10 TB outbound a month. | No expiry. Idle VMs "may be reclaimed" (see below). | **Yes**, for sign-up. A small temporary hold. Not charged unless you upgrade. | Yes. The whole stack fits in 12 GB. Rendering is slow on 2 cores. | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [sign-up](https://docs.oracle.com/en-us/iaas/Content/GSG/Tasks/signingup_topic-Sign_Up_for_Free_Oracle_Cloud_Promotion.htm) |
| Oracle Always Free (AMD micro) | Two VMs, each 1/8 OCPU and 1 GB. | Same idle rule. | Yes | No. Too small. | Same page |
| **Google Cloud e2-micro** | One e2-micro VM in us-west1, us-central1 or us-east1. 30 GB disk. 1 GB outbound a month. | No expiry. | **Yes** (billing account). | No. About 1 GB RAM (not verified on the page). Cannot render. | [GCP free features](https://docs.cloud.google.com/free/docs/free-cloud-features) |
| **Google Cloud Run** | 2 million requests, 180,000 vCPU-seconds, 360,000 GB-seconds a month. | Scales to zero. With request-based billing the CPU is only given during a request. | Yes | API: yes. Render service: yes (about 25 hours a month of 2 vCPU / 4 GB — my arithmetic). Celery workers: **no** on request-based billing. | [GCP free features](https://docs.cloud.google.com/free/docs/free-cloud-features), [billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings) |
| Google Cloud new-customer credit | $300 for 90 days. | Ends after 90 days. | Yes | Good for a short GKE exercise. | [GCP free features](https://docs.cloud.google.com/free/docs/free-cloud-features) |
| **AWS (accounts made after mid-2025)** | $100 credit at sign-up. Up to $100 more for doing set tasks. Free-tier EC2 types: t3.micro, t3.small, t4g.micro, t4g.small, c7i-flex.large, m7i-flex.large and others. | Free plan ends **after 6 months or when credit runs out**. Account is then closed. Data is kept 90 days. | "Not required for most new customers." AWS may still ask. | Yes, on one m7i-flex.large (2 vCPU, 8 GB). See option C. | [AWS Free Tier](https://aws.amazon.com/free/), [billing guide](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html), [FAQ](https://aws.amazon.com/free/free-tier-faqs/), [EC2 free tier](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html) |
| AWS Lightsail | Listed as "free to start" under the Free Tier. The page does not say which bundle or for how long. Paid: 2 GB $12, 4 GB $24, 8 GB $44 a month. | Not stated. | — | Paid from credit. Simpler than EC2, less useful on a résumé. | [Lightsail pricing](https://aws.amazon.com/lightsail/pricing/) |
| **Azure free account** | $200 credit for 30 days. Then 12 months of 750 hours a month of B1s, B2pts v2 (Arm) or B2ats v2 (AMD). | Credit lost after 30 days. You must move to pay-as-you-go within 30 days to keep the 12-month services. | Not verified (the page did not say; I believe yes). | Weak. The free VMs are small burstable types. Sizes not verified. | [Azure free services](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/create-free-services), [Azure free page](https://azure.microsoft.com/en-us/pricing/free-services) |
| **Fly.io** | $1 credit with no card. **$5 of usage every month once you add a card.** Free plan allows up to 3 Machines. | Stopped Machines cost only disk ($0.15/GB a month). | No for $1. Yes for $5 a month. | Partly. 3 small Machines fit in $5. A 2 vCPU / 4 GB render Machine costs about $0.035 an hour while running (my arithmetic from their rates). | [Fly pricing](https://docs.fly.io/about/pricing) |
| **Render.com** | Free web service: 0.1 CPU, 512 MB. 750 instance-hours a month. | **Sleeps after 15 minutes** with no inbound traffic. Free Postgres is deleted 30 days after creation. | Not stated. | API only. The pricing FAQ names only web services, Postgres and Key Value as free. Workers start at $7 a month. | [Render free](https://render.com/docs/free), [Render pricing](https://render.com/pricing) |
| **Railway** | $5 one-time trial credit, 30 days. Then a Free plan with $1 of usage a month, 0.5 GB RAM, 1 vCPU. | Services stop when the trial ends. | No | No. Too small for workers plus render. | [Railway plans](https://docs.railway.com/reference/pricing/plans), [free trial](https://docs.railway.com/reference/pricing/free-trial) |
| **Koyeb** | One free web service: 0.1 vCPU, 512 MB, 2 GB disk. Frankfurt or Washington only. | **Scales to zero after 1 hour** without traffic. | Yes. A $29 hold that is then cancelled. | API only. "Free Instances can't be used as Worker Services." | [Koyeb instances](https://www.koyeb.com/docs/reference/instances), [Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing) |
| **Northflank** | Sandbox: 2 services, 2 jobs, 1 database. "Always on, no sleeping." | No sleep. | **Yes** | Partly. Sizes are "limited" and not listed. Two services is not enough for API + 3 workers + render. | [Northflank pricing](https://northflank.com/pricing) |
| **Hugging Face Spaces (Docker)** | CPU Basic hardware (2 vCPU, 16 GB) has no hourly cost, **but creating a Docker Space now needs a paid plan** (PRO, $9 a month). | Free hardware sleeps when unused. Disk is not persistent. | Paid plan | No longer a free option. | [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [HF pricing](https://huggingface.co/pricing) |

### Oracle details you must know

- **The limit was cut in half in 2026.** Oracle's page now says 1,500 OCPU-hours and 9,000 GB-hours, "equivalent to 2 OCPUs and 12 GB". Verified on Oracle's page. ([source](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm))
- Reported: the change took effect on 2026-06-15 with no announcement. Other reports say enforcement began on 2026-08-18. Free-only accounts above the limit were shut down until resized. Support staff told some users that pay-as-you-go accounts keep 4 OCPU / 24 GB. Oracle has not confirmed this. ([InfoQ](https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/))
- **Capacity.** Oracle's own page says an "out of host capacity" error means "a temporary lack of Always Free shapes in your home region". You may wait hours or days to get a VM. You cannot change your home region later (not verified today).
- **Idle reclaim.** A VM counts as idle if, over 7 days, **all** of these are true: CPU 95th percentile under 20%, network under 20%, memory under 20% (memory applies to A1 only). 20% of 12 GB is 2.4 GB. Postgres + Redis + API + four Celery processes + the render service will likely use more than that, which keeps the VM safe. Check with `free -m` after deploy.
- **Trust.** Oracle changed the free tier once without notice. It can do so again.

---

## 4. Kubernetes

| Option | Control plane cost | Node cost | Card | Notes | Source |
|---|---|---|---|---|---|
| **k3s on one free VM** | $0 | $0 on the Oracle VM | As for the VM | Real Kubernetes API. Uses about 0.5–1 GB RAM itself (not verified). Best free choice. | — |
| **kind / k3d / minikube on your PC** | $0 | $0 | No | Start here. Same manifests work later on k3s. | — |
| **GKE** | $74.40 credit a month per billing account. Covers one zonal or Autopilot cluster fee ($0.10/hour). | **Not free.** Credit does not cover nodes or pods. | Yes | Use the $300 / 90-day credit for a short exercise. | [GCP free features](https://docs.cloud.google.com/free/docs/free-cloud-features), [GKE pricing](https://cloud.google.com/kubernetes-engine/pricing) (amount read through search results; the page itself was cut off) |
| **Oracle OKE** | Basic cluster listed as "Free". Enhanced cluster $0.10/hour. | A1 nodes inside the Always Free limit should cost $0, but I found no Oracle page that says so. | Yes | Not verified: the pricing page blocked my fetch (HTTP 403). Reported by search results only. Many guides say OKE needs a pay-as-you-go account. | [OKE pricing](https://www.oracle.com/cloud/cloud-native/kubernetes-engine/pricing/), [basic vs enhanced](https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcomparingenhancedwithbasicclusters_topic.htm) |
| **Azure AKS** | Free tier: "Free cluster management." No SLA. | Pay for nodes. | Azure account | Nodes come from the $200 / 30-day credit or the small 12-month VMs. | [AKS pricing tiers](https://learn.microsoft.com/en-us/azure/aks/free-standard-pricing-tiers) |
| **Civo** | Free | From $5.43 a month (1 core, 1 GB). | Not stated | "$250 of free credit for the first month you sign up." | [Civo pricing](https://www.civo.com/pricing) |
| **AWS EKS** | **$0.10/hour, about $73 a month. No free tier.** | Pay for nodes. | — | Eats a $100 credit in about 5 weeks before nodes. Do it as a 2–3 day exercise, then delete. | [EKS pricing](https://aws.amazon.com/eks/pricing/) |

**Conclusion:** a managed control plane can be free, but worker nodes are not, except Oracle's. For a cluster that stays up at $0, use k3s on one VM.

---

## 5. Frontend (static site)

| Provider | Free limits | Card | Notes | Source |
|---|---|---|---|---|
| **Cloudflare Pages** | 500 builds a month. 20,000 files. 25 MiB per file. 100 custom domains. No bandwidth cap stated. | No (not stated on page) | Best fit. Gives a fixed `*.pages.dev` HTTPS URL. | [Pages limits](https://developers.cloudflare.com/pages/platform/limits/) |
| Vercel Hobby | 100 GB transfer, 1M requests, 100 deploys a day. | No | Paused for up to 30 days if you pass a limit. Hobby is for non-commercial use (not verified today). | [Vercel Hobby](https://vercel.com/docs/plans/hobby) |
| Netlify Free | 300 credits. A production deploy costs 15 credits. Bandwidth costs 20 credits per GB. | Not stated | About 20 deploys if you use nothing else. The page does not say what happens at zero. | [Netlify pricing](https://www.netlify.com/pricing/) |
| GitHub Pages | 1 GB site. 100 GB a month soft limit. 10 builds an hour. | No | Not allowed for running a commercial SaaS. Fine for a portfolio. | [GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) |

**Pick:** Cloudflare Pages. Remember to set SPA fallback routing and the API base URL at build time.

---

## 6. PostgreSQL

| Provider | Free limits | Pause / expiry | Connections | citext + pgcrypto | Card | Source |
|---|---|---|---|---|---|---|
| **Neon** | 1 GB per project. 100 CU-hours per project a month. Up to 2 CU. 5 GB egress. | **Scales to zero after 5 minutes idle. Cannot be turned off.** If CU-hours run out, compute is suspended until next month. Data is kept. | 112 direct at 0.25 CU. 10,000 through the pooler. | **Yes, both, on PG16** (citext 1.6, pgcrypto 1.3). | No | [Neon pricing](https://neon.com/pricing), [extensions](https://neon.com/docs/extensions/pg-extensions), [pooling](https://neon.com/docs/connect/connection-pooling) |
| **Supabase** | 500 MB database. 2 projects. Shared CPU, 0.5 GB RAM. 5 GB egress. | **Paused after 1 week of inactivity.** | 60 direct. 200 pooler clients, pool size 15. | Not verified today. Both are standard extensions that Supabase has long offered. Postgres version offered also not verified. | Not stated | [Supabase pricing](https://supabase.com/pricing), [compute](https://supabase.com/docs/guides/platform/compute-and-disk) |
| **Aiven free PostgreSQL** | 1 CPU, 1 GB RAM, 1 GB storage. No choice of region. | "Powers off after a period of inactivity", with notice by email. | 20 max. No connection pooling. | Not verified. The page lists only PostGIS, TimescaleDB and pgvector. | No | [Aiven free Postgres](https://aiven.io/free-postgresql-database), [free plan docs](https://aiven.io/docs/platform/concepts/free-plan) |
| CockroachDB Cloud | The pricing page now shows only a 30-day trial with $400 credit. I could not find the old free Basic plan. | Cluster paused after the trial. | — | Not real Postgres. citext is not supported (not verified today). **Do not use.** | No for trial | [Cockroach pricing](https://www.cockroachlabs.com/pricing/) |
| Render Postgres | 1 GB | **Deleted 30 days after creation** (14-day grace). | — | — | — | [Render free](https://render.com/docs/free) |
| Koyeb Postgres | 1 GB, 0.25 vCPU | **5 hours of active time** only. | — | — | Yes | [Koyeb pricing](https://www.koyeb.com/pricing) |
| **Self-hosted in Docker on the VM** | Limited by the VM disk | None | As configured | Yes. Same `postgres:16` image as local. | — | — |

**Neon warning for this app.** 100 CU-hours at 0.25 CU is 400 hours of running compute (my arithmetic). A month has about 730 hours. If Celery beat or a worker touches the database every few minutes, Neon never sleeps and the quota ends near day 17. Either keep periodic tasks off the database, or host Postgres on the VM.

**Pick:** self-hosted Postgres on the VM for options A and C, with a nightly `pg_dump` to R2. Neon for option B.

---

## 7. Redis

| Provider | Free limits | Fits a Celery broker? | Card | Source |
|---|---|---|---|---|
| **Upstash** | 500,000 commands a month. 250 MB. 10 GB bandwidth. 1 database. | **No.** Upstash's own Celery page says: "Celery workers poll Redis continuously, even when there is no queue activity", and it recommends a paid Fixed plan ($10 a month for 250 MB). My estimate: one idle worker polling once a second sends about 2.6 million commands a month. Three workers plus beat send far more. At the limit you get `ERR max requests limit exceeded`. | No | [Upstash pricing](https://upstash.com/pricing/redis), [Upstash Celery](https://upstash.com/docs/redis/integrations/celery) |
| **Redis Cloud free** | 30 MB. **30 concurrent connections.** One database. | Risky. Three workers, beat and the API can pass 30 connections. Memory is small but enough for a queue. No command cap stated. | Not stated | [Redis Cloud plans](https://redis.io/docs/latest/operate/rc/subscriptions/), [Redis pricing](https://redis.io/pricing/) |
| **Aiven free Valkey** | 1 CPU, 1 GB RAM. No choice of region. | Yes on paper. No command cap. Powers off when "not actively used"; a polling worker keeps it active. Latency depends on the region Aiven picks. | No | [Aiven free Valkey](https://aiven.io/free-valkey-database) |
| Render Key Value free | 25 MB. 50 connections. In memory only; data is lost on restart. | Only for an app also on Render. | Not stated | [Render pricing](https://render.com/pricing), [Render free](https://render.com/docs/free) |
| **Self-hosted in Docker on the VM** | VM memory | **Yes. Best choice.** No limits, no network hop, same image as local. | — | — |

**Pick:** Redis as a container next to the workers. Upstash is fine for rate limits and idempotency keys alone, but not as the broker.

---

## 8. Object storage (S3-compatible)

| Provider | Free limits | Egress | Card | Source |
|---|---|---|---|---|
| **Cloudflare R2** | 10 GB-month. 1 million Class A and 10 million Class B operations a month. Standard storage only. | Free | Not stated on the page. I believe a payment method is needed to turn R2 on (not verified). | [R2 pricing](https://developers.cloudflare.com/r2/pricing/) |
| **Backblaze B2** | First 10 GB free. | Free up to 3× stored data, then $0.01/GB. | Not stated | [B2 pricing](https://www.backblaze.com/cloud-storage/pricing) |
| **Oracle Object Storage** | 20 GB on an Always-Free-only account (10 GB per tier on trial or paid). 50,000 API requests a month. | 10 TB a month | Oracle account | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |
| Supabase Storage | 1 GB. **50 MB maximum file size.** | 5 GB | Not stated | [Supabase pricing](https://supabase.com/pricing) |
| Tigris | 5 GB. 10,000 write-type requests a month. | Free | Not verified | [Tigris pricing](https://www.tigrisdata.com/pricing/) (read through search results; my direct fetch failed) |
| Google Cloud Storage | 5 GB in US regions. 5,000 Class A operations. | 100 GB from North America | Yes | [GCP free features](https://docs.cloud.google.com/free/docs/free-cloud-features) |

**Pick:** Cloudflare R2. 10 GB and free egress suit video files. The 50 MB cap rules out Supabase for videos. Oracle's 50,000 requests a month is low.

---

## 9. Email

| Provider | Free limits | SMTP | Notes | Source |
|---|---|---|---|---|
| **Resend** | 3,000 a month. **100 a day.** 3 domains. | Yes | You need your own domain to send to other people (not verified today). | [Resend pricing](https://resend.com/pricing) |
| **Brevo** | 300 a day. | Yes | Read on Brevo's feature pages through search results. The pricing page did not load. | [Brevo free SMTP](https://www.brevo.com/free-smtp-server), [Brevo email API](https://www.brevo.com/features/email-api/) |
| Mailgun | 100 a day. 1 domain. 1 day of logs. | Yes | | [Mailgun pricing](https://www.mailgun.com/pricing/) |
| AWS SES | No separate free quota now. $0.10 per 1,000 emails, paid from the Free Tier credit. | Yes | New accounts start in a sandbox and can only send to verified addresses until AWS approves them (not verified today). | [SES pricing](https://aws.amazon.com/ses/pricing/) |
| Oracle Email Delivery | 3,000 a month, Always Free. | Yes (not verified) | Only useful with option A. | [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |
| Gmail SMTP with an app password | About 500 a day (not verified). | Yes | Works with no domain. Fine for a demo. Mail comes from your Gmail address. | — |

**Pick:** Brevo or Resend if you have a domain. Gmail SMTP if you do not. Use port 587; most clouds block outbound port 25 (not verified today).

---

## 10. Architectures, ranked

### Rank 1 — Option A: everything on one Oracle Always Free VM

| Item | Detail |
|---|---|
| What runs where | One `VM.Standard.A1.Flex`, 2 OCPU, 12 GB, Ubuntu 24.04 arm64. Docker Compose (later k3s) runs API, 3 workers, beat, render service, Postgres 16, Redis, and Caddy for HTTPS. Frontend on Cloudflare Pages. Files on R2. Email by Brevo, Resend or Oracle Email Delivery. |
| Monthly cost | $0. A domain name is optional, about $10 a year. |
| What sleeps or breaks | Nothing sleeps. Rendering uses both cores, so the API is slow while a video renders. Set CPU limits on the render container. |
| Card | Yes, for Oracle sign-up. Not charged. |
| Main risk | You may not get the VM ("out of host capacity"). Oracle may change the terms again. |

**arm64 work needed:**

- Every image must be built for `linux/arm64`. Build on the VM itself, or use `docker buildx build --platform linux/arm64`. Building on an x86 PC through emulation is slow.
- Python: most wheels exist for arm64. Any package without one compiles from source and needs build tools in the image. Check `requirements.txt` on the VM.
- Remotion: the docs list Linux with glibc 2.35 or newer (Ubuntu 22.04+, Debian) and mention arm64 support. Use a Debian or Ubuntu base image. **Alpine is not supported.** ([Remotion Linux dependencies](https://www.remotion.dev/docs/miscellaneous/linux-dependencies)) I did not test a render on arm64. Test one before you depend on it.
- Do not install x86 Google Chrome. Let Remotion download its own Chrome Headless Shell for arm64.
- Expect renders to take about twice as long as on a 4-core PC (my estimate).

**Other steps:** open ports 80 and 443 in both the Oracle security list and the VM firewall. Add a nightly `pg_dump` to R2. Check memory use is above 2.4 GB so the idle rule cannot apply.

### Rank 2 — Option C: AWS on the new-account credit

Two forms. Start with C-lite.

**C-lite (recommended form): one EC2 VM with Docker Compose**

| Item | Detail |
|---|---|
| What runs where | One `m7i-flex.large` (2 vCPU, 8 GB, x86) with a 40 GB gp3 disk and one Elastic IP. The existing Compose file runs everything. S3 instead of MinIO, or keep R2. Frontend on Cloudflare Pages, or S3 + CloudFront for the résumé. |
| Monthly cost | About $77 if always on: $70 for the VM ($0.096/hour), $3.65 for the public IPv4 ($0.005/hour), about $3 for disk. All paid by credit. About $18 if you run it 4 hours a day and stop it otherwise. Arithmetic is mine; disk rate not verified. |
| How long it lasts | Always on: about 5 weeks on $100, about 11 weeks on $200. Stopped when idle: the full 6 months. |
| What sleeps or breaks | Nothing while running. When stopped, the demo is down. Keep the Elastic IP so the OAuth redirect URL does not change. |
| Card | Not required for most new sign-ups, per AWS. |
| Main risk | **The Free plan account closes at 6 months or when credit hits zero.** Data is kept 90 days. To keep going you must upgrade to a paid plan and pay about $77 a month. |

Sources: [EC2 free tier types](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html), [m7i-flex.large price](https://instances.vantage.sh/aws/ec2/m7i-flex.large), [c7i-flex.large price](https://instances.vantage.sh/aws/ec2/c7i-flex.large), [IPv4 price](https://aws.amazon.com/vpc/pricing/). Instance prices come from Vantage, a third-party price list, because the AWS pricing table did not load.

Notes:

- `t3.small` (2 GB, about $15 a month) is too small for the render service.
- I assume a Free plan account can only launch the free-tier-eligible instance types. Not verified.
- On the Free plan you are never billed. That makes it safe for learning.

**C-full (résumé form): managed AWS services**

ECS Fargate for API and workers, RDS PostgreSQL, ElastiCache, S3, CloudFront, SES, an Application Load Balancer. This is the "real" AWS design. It costs well over $100 a month (my estimate; not priced line by line), so the credit lasts only a few weeks. Build it once as a learning exercise with a budget alarm, take screenshots and keep the Terraform or CloudFormation files, then delete it. Whether every one of these services is open to Free plan accounts is not verified.

### Rank 3 — Option B: managed free tiers stitched together

| Part | Where | Limit that hurts |
|---|---|---|
| Frontend | Cloudflare Pages | None |
| API | Render free web service, or Koyeb free, or Cloud Run | Render sleeps after 15 minutes. Koyeb after 1 hour. First request then takes many seconds (cold-start time not verified). |
| Workers + beat | **No free home.** Fly.io with a card ($5 a month included), or Render at $7 a month each. | Free hosts allow web services only. |
| Render service | **No free home without a card.** Cloud Run (card) fits inside the free quota for about 25 render-hours a month. Or a Fly Machine that starts on demand. | Needs 2 vCPU and 2–4 GB. |
| Postgres | Neon | Sleeps after 5 minutes. 100 CU-hours a month. |
| Redis | Aiven free Valkey. Not Upstash. | Extra network hop for every poll. |
| Storage | R2 | None |
| Email | Brevo or Resend | 300 or 100 a day |

| Item | Detail |
|---|---|
| Monthly cost | $0 only if you accept a card on file (Fly and/or Google Cloud) and merge the three workers and beat into one process (`celery worker -B -Q general,render,publish`). Otherwise $7–$25 a month. |
| What sleeps or breaks | The API sleeps. Neon sleeps. A 50-minute job dies if its host is put to sleep. Five dashboards and five sets of secrets to manage. |
| Main risk | Any one provider changes its free tier and the app stops. This happened three times in 2026 (Oracle, Hugging Face, and AWS a year earlier). |

A workable version of B, if you want one: Fly.io with a card. Machine 1 is the API. Machine 2 is one Celery worker with beat and a Redis container (or Aiven Valkey). Machine 3 is the render service, 2 vCPU / 4 GB, set to stop when idle. Neon, R2, Pages and Brevo for the rest. This should stay inside Fly's $5 a month if the render Machine runs only a few hours (my estimate). Fly's free plan caps you at 3 Machines.

B teaches the least about Docker and Kubernetes, and has the most parts. It ranks last for this app. Its data-layer picks (Pages, R2, email) are still worth using with A and C.

---

## 11. Recommendation

### (1) Public demo up tomorrow, least friction

**Use C-lite: one AWS EC2 `m7i-flex.large` with the existing Docker Compose file.**

- Same x86 images as your PC. No arm64 work.
- AWS says most new accounts need no card.
- No waiting for capacity.
- Steps: create the account on the Free plan → launch Ubuntu 24.04 on `m7i-flex.large` with 40 GB gp3 → attach an Elastic IP → install Docker → copy the repo and `.env` → `docker compose up -d` → put Caddy in front for HTTPS → deploy the frontend to Cloudflare Pages → add the HTTPS URL to the Google OAuth redirect list.
- Stop the instance when you are not showing it. Set an AWS Budget alert on day one (this also earns extra credit, per AWS's "earn credits" tasks; the exact tasks were not verified).

**Even faster, for a one-off showing:** keep the app on your PC and run a Cloudflare Quick Tunnel. It needs no account and gives an HTTPS URL. But "the hostname changes each time you create a Quick Tunnel", so the Google OAuth redirect breaks on every restart, and it is "for testing and development" only. ([Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/))

**The HTTPS hostname.** Google OAuth needs a fixed HTTPS redirect URL. A domain (about $10 a year) with a DNS record to the VM is the clean way. Free hostname services such as DuckDNS or sslip.io also work with Caddy and Let's Encrypt, but whether Google accepts them as redirect hosts was not verified.

### (2) Best learning value for Docker and Kubernetes

**Use Option A, in stages:**

1. **Now, on your PC:** learn Kubernetes with kind or k3d. Convert the Compose file to manifests (Deployments, Services, a StatefulSet for Postgres, Secrets, an Ingress).
2. **Oracle VM, Docker Compose:** sign up for Oracle today, because getting the VM can take days. Deploy with Compose. You learn multi-arch image builds, a registry (GitHub Container Registry is free for public images — not verified today), a reverse proxy, TLS, backups.
3. **Same VM, k3s:** replace Compose with k3s. You learn Ingress, persistent volumes, Helm, rolling updates, resource limits. 12 GB is enough for k3s and the whole app.
4. **Optional, for the résumé:** a 2–3 day EKS exercise on leftover AWS credit, or GKE on Google's $300 credit. Delete the cluster afterwards.

If Oracle refuses the sign-up or never has capacity, do stages 2 and 3 on the AWS VM until the credit ends.

### Suggested order

1. Today: create the AWS account and the Oracle account. Deploy on AWS (C-lite). Put frontend on Cloudflare Pages and files on R2.
2. This week: get the Oracle VM. Build arm64 images. Test one Remotion render on it.
3. When the Oracle VM works: point DNS at it, restore the `pg_dump`, stop the AWS instance.
4. Later: move from Compose to k3s on the Oracle VM.

Because files are in R2 and the frontend is on Pages, the move in step 3 is only a database dump, a restore and a DNS change.

---

## 12. What I could not verify

| Item | Status |
|---|---|
| Whether Oracle pay-as-you-go accounts still get 4 OCPU / 24 GB free | Reported by users. Oracle's page says "all tenancies" get the lower limit. |
| The exact date Oracle's cut took effect | Reports give both 2026-06-15 and 2026-08-18. |
| Oracle trial credit amount and length; whether prepaid or virtual cards work | Not on the pages I could read. The Oracle FAQ page blocked my fetch. |
| OKE basic cluster is free, and whether A1 worker nodes stay free under OKE | Read only in search results. Oracle's pricing page returned HTTP 403. |
| A real Remotion render on arm64 | Docs indicate support. Not tested. |
| Which AWS services a Free plan account can use (RDS, ECS, EKS, ElastiCache, SES, Lightsail), and whether it can launch non-eligible EC2 types | AWS pages point to a list that I could not load. |
| The tasks that earn the second $100 of AWS credit | AWS says "completing activities". The list was not read. |
| EC2 hourly prices | Taken from Vantage, not from AWS. gp3 disk price not confirmed for US East. |
| Lightsail free offer (which bundle, how long) | The page does not say. |
| Azure: card requirement, size of the free VMs, free Postgres offer | Not on the pages read. |
| Cloud Run prices beyond the free quota, and whether the free quota covers worker pools | The pricing page was cut off. |
| GKE $74.40 credit | Read through search-result summaries of Google's page. |
| Supabase: citext and pgcrypto, Postgres versions | The docs page read does not name extensions. |
| Aiven free Postgres: citext and pgcrypto, exact inactivity period | Not stated on the pages read. |
| CockroachDB free Basic plan | Not on the current pricing page. May have moved or ended. |
| Whether Cloudflare R2 needs a payment method to turn on | Not stated on the pricing page. |
| Tigris free tier numbers | Read through search results. |
| Brevo 300 emails a day | Read on Brevo feature pages through search results; the pricing page did not load. |
| Resend needing your own domain; SES sandbox rules; Gmail SMTP daily limit; port 25 blocking | From general knowledge. |
| Upstash command count for Celery | My estimate. Upstash confirms the polling problem but gives no number. |
| Neon running out near day 17 | My arithmetic from the 100 CU-hour limit. |
| Cold-start times, k3s memory use, render speed on 2 Arm cores | My estimates. |
| Northflank Sandbox instance sizes | The page says "limited" with no numbers. |
