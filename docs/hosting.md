# Hosting QRESPONDER

QRESPONDER is a **local, single-user tool** by default: it binds `127.0.0.1`, has no
auth, and nothing leaves the host. That's the intended way to run it, and you don't
need this page for it.

This page is for the *optional* case where you want to reach it from other machines —
e.g. host it for your team on a cloud VM/VPC, the way you'd host any internal app.
It's designed to be **one command with automatic HTTPS**.

> ⚠ **A no-auth instance on a network exposes your entire knowledge base.** The hosted
> setup below fixes that with **TLS + an access token + the app kept off the public
> interface** — don't shortcut it.

---

## One-command hosted stack (HTTPS, anywhere)

The `docker-compose.hosted.yml` stack runs the app **internally** and puts **Caddy** in
front for automatic Let's Encrypt TLS. You set three values and run one command.

**Prerequisites:** a VM with Docker + a domain (or subdomain) whose DNS **A record**
points at the VM's public IP, and ports **80** and **443** open.

```bash
# On the VM:
curl -fsSLO https://raw.githubusercontent.com/scorpionus007/QResponder/main/docker-compose.hosted.yml
curl -fsSLO https://raw.githubusercontent.com/scorpionus007/QResponder/main/Caddyfile
curl -fsSLO https://raw.githubusercontent.com/scorpionus007/QResponder/main/.env.example
mv .env.example .env

# Edit .env and set at least:
#   QR_DOMAIN=qresponder.yourcompany.com
#   QR_ACME_EMAIL=you@yourcompany.com
#   QRESPONDER_AUTH_TOKEN=<paste `openssl rand -hex 24`>
#   (+ your model — a local Ollama, or a cloud provider key)

docker compose -f docker-compose.hosted.yml up -d
```

Then open **`https://<QR_DOMAIN>/?token=<QRESPONDER_AUTH_TOKEN>`** once. The app sets a
cookie and you're in; share the URL (with the token) only with people who should have
access. Caddy fetches and renews the certificate automatically.

**Why this is safe:** the app container is **not** published to the host (only Caddy's
80/443 are), and the app **requires the token** — so there are two layers between the
internet and your KB, over TLS.

---

## GCP walkthrough (Compute Engine / VPC)

This is the exact flow to host it in a GCP project/VPC:

1. **Create a VM.** Compute Engine → Create instance. An `e2-small`/`e2-medium` is
   plenty (bump RAM if you'll run a local model on the same box). Boot disk: Ubuntu
   22.04+. Under **Firewall**, tick **Allow HTTP** and **Allow HTTPS** (or add a VPC
   firewall rule allowing `tcp:80,443` from the ranges you want — e.g. your office/VPN
   CIDR only, for an internal tool).
2. **Reserve/point DNS.** Give the VM a static external IP (VPC network → IP addresses),
   then add a DNS **A record** for `qresponder.yourcompany.com` → that IP.
3. **Install Docker** (SSH into the VM):
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
4. **Run the stack** — the "One-command hosted stack" block above.
5. **Open** `https://qresponder.yourcompany.com/?token=…`. Done.

For an **internal-only** deployment (recommended for a security tool): instead of
opening 80/443 to the world, restrict the VPC firewall rule to your corporate/VPN CIDR,
or keep the VM on a private subnet and reach it over your VPN / an internal load
balancer. The token + TLS still apply.

Same steps work on **AWS EC2**, **Azure VM**, **DigitalOcean**, Hetzner, or a bare VM —
only the "create VM + firewall + static IP + DNS" UI differs.

### AWS EC2

1. Launch an instance (Ubuntu 22.04+, `t3.small`+). In its **security group**, allow
   inbound `80` and `443` (from `0.0.0.0/0` for public, or your office/VPN CIDR for
   internal). Allocate + associate an **Elastic IP**.
2. Point a DNS **A record** at the Elastic IP.
3. SSH in, install Docker (step 3 above), run the one-command stack.

### Azure VM

1. Create a VM (Ubuntu). In the VM's **Networking / NSG**, add inbound rules for
   `80` and `443`. Give it a **static public IP** (or a private one for internal use).
2. Point DNS at the IP.
3. SSH in, install Docker, run the one-command stack.

---

## No Docker? Run it with systemd

If you'd rather run the app directly (no Docker), install it and add a service unit.
Put a reverse proxy (Caddy/nginx) in front for TLS, and set `QRESPONDER_AUTH_TOKEN`.

```bash
sudo useradd -r -m -d /opt/qresponder qr
sudo -u qr python3 -m venv /opt/qresponder/venv
sudo -u qr /opt/qresponder/venv/bin/pip install "qresponder[web,retrieval]"
```

`/etc/systemd/system/qresponder.service`:

```ini
[Unit]
Description=QRESPONDER web UI
After=network.target

[Service]
User=qr
WorkingDirectory=/opt/qresponder
EnvironmentFile=/opt/qresponder/.env
# Bind loopback and let your reverse proxy terminate TLS + reach it.
ExecStart=/opt/qresponder/venv/bin/qresponder serve --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now qresponder
```

---

## A note on the published image

The published image `ghcr.io/scorpionus007/qresponder` is **slim** (no torch): retrieval
mode runs via a **BM25 fallback**, which is fine for keyword-heavy questionnaire
content. For full **dense hybrid retrieval + cross-encoder rerank** on a large KB,
either build with the retrieval extra
(`docker build --build-arg EXTRAS=anthropic,openai,web,retrieval …`) or, for the
systemd path, the `pip install "qresponder[web,retrieval]"` above already includes it.

---

## Bring your own reverse proxy (nginx / Traefik)

If you already run a proxy, skip Caddy: run the app bound to loopback (the default
`docker-compose.yml`, or `qresponder serve`), set `QRESPONDER_AUTH_TOKEN`, and proxy to
`127.0.0.1:8000`. The app enforces the token; your proxy adds TLS (and optionally its
own auth). The proxy can inject the token so browsers don't need it:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Authorization "Bearer ${QRESPONDER_AUTH_TOKEN}";
    proxy_set_header Host $host;
}
```

Traefik: a `Host()` router with a TLS resolver + a headers middleware injecting the
`Authorization: Bearer` token.

---

## The access token, in detail

`QRESPONDER_AUTH_TOKEN` — when set, **every** request to the app + API must carry it
(unset by default = the local no-auth experience). Supply it via:

- **`Authorization: Bearer <token>`** header (what a proxy injects), or
- opening **`https://host/?token=<token>`** once (the app sets an `httponly`,
  `samesite=strict` cookie).

It's compared in constant time and never appears in a response body or log. The
`/healthz` liveness endpoint stays open so container/uptime checks work.

## What still holds when hosted

- **Answering stays local**: with a local model + local embeddings/reranker, the
  answering path makes **zero external calls**; a cloud provider key (if used) stays in
  `.env` on the server, never in the browser.
- **Connector secrets stay server-side** and are never returned to the browser.
- Connectors fetch **only** on explicit test/sync — never during answering.
- **No telemetry.**

If the KB must never be reachable off-host at all, don't host it — reach the local
default over SSH: `ssh -L 8000:127.0.0.1:8000 the-vm`, then open `http://127.0.0.1:8000`.
