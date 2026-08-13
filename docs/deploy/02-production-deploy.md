# Phase 2: Production Deploy Setup

After Phase 1 (VM provisioned, Docker installed, DNS set), follow these steps
to get the app running and CI/CD deploying on push to `main`.

## 1. Clone the repo on the VM

```bash
ssh deploy@<YOUR_VM_IP>
cd ~
git clone https://github.com/Zian00/notes-rag.git
cd notes-rag
```

## 2. Create the production `.env`

```bash
cp .env.production.example .env
nano .env
```

Fill in real values:
- `POSTGRES_PASSWORD` — generate with `openssl rand -hex 24`
- `JWT_SECRET` — generate with `openssl rand -hex 32`
- `OPENAI_API_KEY` — your OpenAI key
- `DOMAIN` — `notesrag.zheng00.me` (already set in the example)

## 3. First deploy (build + start)

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Watch the logs to confirm everything starts:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

Once Caddy logs show the TLS certificate was obtained, visit
`https://notesrag.zheng00.me` — you should see the app.

## 4. Set up GitHub Actions CD

The deploy workflow (`.github/workflows/deploy.yml`) needs two repository
secrets:

1. **`VM_HOST`** — the VM's public IP address
2. **`VM_SSH_KEY`** — a private SSH key that can log into the VM as `deploy`

### Generate a deploy key (on your local machine)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/notes-rag-deploy -N ""
```

### Add the public key to the VM

```bash
ssh-copy-id -i ~/.ssh/notes-rag-deploy.pub deploy@<YOUR_VM_IP>
```

### Add the secrets to GitHub

1. Go to `github.com/Zian00/notes-rag` → Settings → Secrets and variables → Actions
2. Add **`VM_HOST`**: paste the VM's IP
3. Add **`VM_SSH_KEY`**: paste the contents of `~/.ssh/notes-rag-deploy` (the private key)

### Test it

Push to `main` and check the Actions tab — the deploy job should SSH in,
pull, build, and restart the containers.

## 5. Verify

```bash
curl -I https://notesrag.zheng00.me
```

Should return `HTTP/2 200` with a valid TLS certificate.

## Redeployment

After the CD is set up, every push to `main` automatically:
1. SSHs into the VM
2. `git pull`
3. Rebuilds changed images
4. Restarts containers
5. Prunes old images
