# Phase 1: Azure VM + DNS Provisioning

Manual steps — do these before Phase 2 (the code changes) can deploy.

## 1. Create the Azure VM

1. Go to **https://portal.azure.com** → "Create a resource" → "Virtual Machine"
2. Fill in:
   - **Subscription**: Azure for Students
   - **Resource group**: Create new → `notes-rag-rg`
   - **VM name**: `notes-rag-vm`
   - **Region**: `(Asia Pacific) East Asia` (Hong Kong)
   - **Image**: Ubuntu Server 24.04 LTS (x64)
   - **Size**: `Standard_B2als_v2` (2 vCPU, 4 GB RAM, ~$38/mo — within $100 credits)
   - **Authentication**: SSH public key
     - Username: `deploy`
     - SSH public key: paste your public key (`cat ~/.ssh/id_rsa.pub` or `cat ~/.ssh/id_ed25519.pub`)
     - If you don't have one: run `ssh-keygen -t ed25519` on your local machine first
3. **Networking** tab:
   - Public IP: Create new (static)
   - NIC network security group: Basic
   - Public inbound ports: Allow selected → check SSH (22), HTTP (80), HTTPS (443)
   - Availability options: No infrastructure redundancy required
   - Security type: Standard
4. Click **Review + Create** → **Create**
5. Once deployed, note the **Public IP address** from the VM's overview page

## 2. Verify ports 80 and 443 are open

If you selected all three ports during VM creation, they're already open. Verify:

1. In the Azure portal, go to your VM → **Networking** → **Network settings**
2. Confirm inbound rules exist for:
   - **Port 22** (SSH) — Allow
   - **Port 80** (HTTP) — Allow (needed for Let's Encrypt ACME challenge)
   - **Port 443** (HTTPS) — Allow (the app's public endpoint)
3. If any are missing, click **+ Create port rule** → **Inbound port rule** to add them

## 3. SSH into the VM and install Docker

```bash
ssh deploy@<YOUR_VM_IP>
```

Then run:

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install Docker (official method)
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the docker group (no sudo needed for docker commands)
sudo usermod -aG docker $USER

# Install Docker Compose plugin
sudo apt install -y docker-compose-plugin

# Log out and back in for the group change to take effect
exit
```

SSH back in and verify:

```bash
ssh deploy@<YOUR_VM_IP>
docker --version        # should show 27.x+
docker compose version  # should show v2.x+
```

## 4. Point your domain to the VM

In **Namecheap**:

1. Log in → **Domain List** → click `zheng00.me` → **Advanced DNS**
2. Add a new record:
   - **Type**: A Record
   - **Host**: `notesrag`
   - **Value**: `<YOUR_VM_IP>`
   - **TTL**: Automatic
3. Save

Wait a few minutes, then verify from your local machine:

```bash
nslookup notesrag.zheng00.me
```

Should return your VM's IP.

## 5. Create a deploy directory on the VM

```bash
ssh deploy@<YOUR_VM_IP>
mkdir -p ~/notes-rag
```

## Done

Once these steps are complete, tell me:
1. The VM's public IP
2. That `nslookup notesrag.zheng00.me` resolves to it
3. That `docker compose version` works on the VM

Then I'll start Phase 2 (the code + CI/CD pipeline).
