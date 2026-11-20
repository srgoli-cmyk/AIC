# Deploying to a server

For one small server: 1 vCPU, 2 GB RAM, x86_64, Ubuntu 26.04 LTS. The example uses the domain
`ailecture.app` and the address `171.22.119.83`; replace them with yours. Commands run as root until
the `deploy` user exists, then as `deploy`.

What runs: `docker-compose.prod.yml` starts Postgres with pgvector, the backend, and Caddy, which gets
the HTTPS certificate by itself and is the only thing reachable from outside.

## 0. Before you start
- **DNS:** an `A` record for `ailecture.app` pointing at `171.22.119.83`. HTTPS can't be issued until
  it resolves: check with `dig +short ailecture.app`.
- **Google sign-in client:** add `https://ailecture.app` as an authorized JavaScript origin and
  `https://ailecture.app/auth/google/callback` as an authorized redirect URI. While the client is in
  **Testing**, only its listed test users can sign in. To open it to every UCSD student, publish it.
  Google then asks for the home page (`https://ailecture.app/`) and privacy policy
  (`https://ailecture.app/privacy`) on a domain you've verified.
- **OpenRouter:** a production key with its own credit limit.

## 1. A deploy user with SSH key login
From your computer, copy your public key to the server's root (or use the one the provider set up):
```bash
ssh-copy-id root@171.22.119.83
```
On the server, as root:
```bash
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
passwd deploy        # a password for sudo only; SSH will not accept passwords
```
Check from a second terminal that `ssh deploy@171.22.119.83` works and `sudo -v` asks for that
password. Then turn off password and root login:
```bash
cat > /etc/ssh/sshd_config.d/10-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t && systemctl restart ssh
```
Keep your current session open until a new `ssh deploy@...` login works.

## 2. Firewall, updates and swap
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y ufw unattended-upgrades git
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo dpkg-reconfigure -plow unattended-upgrades   # answer Yes: security updates install by themselves
```
Docker publishes ports itself and skips ufw. That's fine here: only Caddy publishes any (80 and
443), and the database and backend have none.

The backend needs about 0.5 GB idle and more while it reads a course, and building the image needs
more still, so add 4 GB of swap:
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-swap.conf
sudo sysctl --system
free -h
```

## 3. Docker
Docker supports Ubuntu 26.04 LTS ("Resolute"). These are the steps from
https://docs.docker.com/engine/install/ubuntu/:
```bash
sudo apt remove $(dpkg --get-selections docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc | cut -f1)
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
```
Log out and back in so the `docker` group applies, then check with `docker run --rm hello-world`.
The first `apt remove` may say none of those packages are installed; that's fine.

## 4. Get the code
Give the server read-only access to the repository with a deploy key:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/aic_deploy -N "" -C "aic deploy"
cat ~/.ssh/aic_deploy.pub
```
Add that key on GitHub under the repository's **Settings → Deploy keys**, without write access. Then:
```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/aic_deploy
  IdentitiesOnly yes
EOF
git clone git@github.com:srgoli-cmyk/AIC.git ~/aic
cd ~/aic
```

## 5. Settings
```bash
cp .env.production.example .env
chmod 600 .env
nano .env
```
Fill in every `CHANGE_ME`:
- `POSTGRES_PASSWORD`: from `openssl rand -base64 32 | tr -d '/+='`.
- `SESSION_SECRET`: from `openssl rand -base64 48`. Use a new value here, not your local one.
- `ACME_EMAIL`, `OPENROUTER_API_KEY`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
- `EXTENSION_ID`: the Chrome Web Store id. Until the extension is published, put the dev id
  `gdgflcpoijokbdnckcojphfoinhkbbmd` and change it later (section 9).

## 6. Start
```bash
docker compose -f docker-compose.prod.yml up -d --build
```
The first build downloads torch and the embedding model and takes a while on one vCPU. Watch it start:
```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend caddy
```
The backend is ready when its status shows `healthy`. Caddy logs that it obtained a certificate for
`ailecture.app`.

## 7. Check it
```bash
curl -fsS https://ailecture.app/health          # {"status":"ok"}
curl -sI http://ailecture.app/ | head -3        # a redirect to https
```
Then, in a browser:
1. `https://ailecture.app/` shows the home page, and `/privacy` the privacy policy.
2. `https://ailecture.app/app/` → **Sign in** with a UCSD account on the client's test-user list.
   You should land on the course page, still empty.
3. A non-UCSD Google account is refused with the "@ucsd.edu" message.

## 8. Daily backups
```bash
mkdir -p ~/backups && chmod 700 ~/backups
~/aic/scripts/backup.sh          # once by hand: it prints the file it wrote
crontab -e
```
Add this line, which runs every night at 03:15 server time and keeps the newest 7:
```
15 3 * * * /home/deploy/aic/scripts/backup.sh >> /home/deploy/backups/backup.log 2>&1
```
A backup on the same disk doesn't survive losing the server, so copy them off regularly, for example
from your own computer:
```bash
rsync -av deploy@ailecture.app:backups/ ./aic-backups/
```
To restore one, stop the backend first:
```bash
docker compose -f docker-compose.prod.yml stop backend
gunzip -c ~/backups/aic-YYYYmmdd-HHMMSS.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U aic -d aic
docker compose -f docker-compose.prod.yml start backend
```

## 9. The extension for students
On your computer:
```bash
make extension-prod DOMAIN=ailecture.app     # writes dist/extension-prod.zip
```
Upload `dist/extension-prod.zip` in the Chrome Web Store developer dashboard, with
`https://ailecture.app/privacy` as the privacy policy. Once the store shows the extension's id, set
it on the server and restart the backend:
```bash
nano ~/aic/.env                               # EXTENSION_ID=<the store id>
docker compose -f docker-compose.prod.yml up -d backend
```

## 10. Updates and rollback
Tag what is running before every update, so you can go back to it:
```bash
cd ~/aic
git tag -f deployed-$(date +%Y%m%d-%H%M)
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```
If the new version misbehaves, go back to the last good tag:
```bash
git tag --list 'deployed-*' | tail -3
git checkout deployed-YYYYmmdd-HHMM
docker compose -f docker-compose.prod.yml up -d --build
```
Then return to `main` (`git checkout main`) once the problem is fixed. Schema changes only ever add
to the database, so an older version runs against a newer database. If one ever doesn't, restore the
backup taken before the update (section 8).

## Day to day
- Logs: `docker compose -f docker-compose.prod.yml logs --tail 200 backend`. Sign-in codes and
  tokens in URLs are shown as `REDACTED`.
- Memory: `docker stats --no-stream`. The limits are 1280 MB for the backend, 384 MB for Postgres and
  128 MB for Caddy.
- Disk: `docker system df`. Remove old images after updates with `docker image prune`.
