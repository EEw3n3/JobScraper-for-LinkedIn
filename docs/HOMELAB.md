# Homelab Deployment

## VM and Runtime

Use an Ubuntu Server 24.04 LTS VM on Proxmox, with no desktop/GUI required.
Minimum: 2 vCPU, 4 GB RAM and 20 GB free disk. Prefer 4 vCPU and 8 GB RAM
when using two browser workers. Keep Ubuntu and Docker security updates current.
Run one web container per set of state directories; do not scale this Compose service.

Install Docker Engine and the Compose plugin using the official Ubuntu instructions:
https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository
After configuring that official apt repository, the package commands are:

```sh
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git openssl
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

If Docker is already installed, check `sudo docker compose version` first. Use the
Compose v2 plugin, not legacy `docker-compose`. Do not mix Ubuntu's `docker.io`
packages with Docker CE packages; follow the official conflict-removal instructions
when migrating an existing install. These commands use `sudo` rather than granting
a login user membership of the effectively root-equivalent Docker group.

## First Deployment

Check out this repository on the VM and enter its root directory. The included
`requirements-web.txt` installs the AI/base requirements, Flask and Waitress.
`python webapp.py` runs Waitress, enforces the password's 16-character minimum,
and provides an unauthenticated, minimal `/healthz` with no secrets.

```sh
cp .env.homelab.example .env.homelab
chmod 600 .env.homelab
openssl rand -hex 32
openssl rand -hex 4
nano .env.homelab
```

Paste the 32-byte value into `JOBPARSER_WEB_PASSWORD`, replacing `CHANGE_ME`, and
the separate 8-character value into `JOBPARSER_VNC_PASSWORD`, replacing `CHANGE`.
The example deliberately fails both checks. Classic VNC authentication only uses
the first eight characters, so keep noVNC behind the encrypted SSH tunnel. Do not
reuse either password. `JOBPARSER_WEB_USER` defaults to `admin`. Set `GEMINI_API_KEY`
and optionally `GEMINI_MODEL` only if using AI analysis; otherwise leave them blank.
AI analysis sends relevant job/CV content to the configured provider.
Never commit `.env.homelab`, include it in an image, or paste rendered Compose
configuration into tickets: runtime environment values include credentials.

Every Compose command below explicitly uses `--env-file .env.homelab`, so Compose
does not load the repository's `.env`. No application `.env` is copied or mounted.
Exported shell variables can override Compose interpolation; use a clean shell
without exported `JOBPARSER_*`, `APP_UID`, `APP_GID` or `GEMINI_*` overrides.

The default process identity is UID 1000 / GID 1000. For a different non-root
identity, set **both** `APP_UID` and `APP_GID` in `.env.homelab` before building,
and replace `1000:1000` in every ownership command below with those same numbers.
Choose unused IDs in the base image; never set UID or GID to 0. Compose passes the
IDs as build arguments and runtime user, so the browser has a matching home user.

```sh
mkdir -p results cv_storage web_state
sudo chown -R 1000:1000 results cv_storage web_state
sudo chmod 700 results cv_storage web_state
sudo docker compose --env-file .env.homelab build --pull
sudo docker compose --env-file .env.homelab up -d
sudo docker compose --env-file .env.homelab ps
curl --fail http://127.0.0.1:8080/healthz
```

Use dedicated application directories. Recursive ownership changes above apply
only to those directories; inspect any pre-existing contents before changing
ownership. Existing data files must also be readable/writable by the selected
UID as appropriate. Never use `chmod 777`. Bind mounts obscure image ownership,
so the host permissions matter. Missing host directories cause startup to fail
instead of being silently created as root.

| Host directory | Container directory | Access |
| --- | --- | --- |
| `./results` | `/app/results` | Read/write job databases and reports |
| `./cv_storage` | `/app/cv_storage` | Read-only CVs |
| `./web_state` | `/app/web_state` | Read/write durable web state |

Upload a CV from your workstation via SSH, then install it on the VM (substitute
your VM login, repository path and filename). Staging in the login home avoids
requiring that login to have direct access to the private application directory:

```sh
# Workstation:
scp ./my-cv.pdf vmuser@VM_IP:~/my-cv.pdf
# VM, from the repository root:
sudo install -o 1000 -g 1000 -m 600 "$HOME/my-cv.pdf" ./cv_storage/my-cv.pdf
rm "$HOME/my-cv.pdf"
```

CVs are managed on the host, not uploaded into the read-only container mount.
Use the configured UID/GID instead of 1000 if changed above.

## Network Access

The application listens on `0.0.0.0:8080` **inside** the container, but Docker
publishes only `127.0.0.1:8080` on the VM by default. From your workstation:

```sh
ssh -N \
  -L 18080:127.0.0.1:8080 \
  -L 16080:127.0.0.1:6080 \
  vmuser@VM_IP
```

Open `http://127.0.0.1:18080` in your workstation browser and log in. The tunnel
encrypts traffic between the workstation and VM. Do not open VM port 8080 for
this setup. Permit SSH only from your management network/VPN in the Proxmox and
VM firewalls; allow SSH before enabling a firewall to avoid locking yourself out.

To see and control Chromium, select **Интерактивный браузер через noVNC** before
starting a scrape or retry, then use **Открыть экран браузера** in the panel. The
default link opens `http://127.0.0.1:16080/vnc.html`; enter
`JOBPARSER_VNC_PASSWORD` when noVNC asks. Interactive mode uses one browser and a
persistent profile under `web_state/chrome-profile`, so cookies and sign-in state
survive browser restarts. On recognized sign-in or verification pages the parser
waits up to `JOBPARSER_INTERACTIVE_WAIT` seconds (default 300) for manual action.
The automation continues after the block disappears; arbitrary clicks can still
race with Selenium, so use manual control mainly for login, consent, and recovery.
The tool does not bypass access controls or guarantee that a source permits login
or automated collection.

For deliberate LAN publishing, set `JOBPARSER_BIND_IP` to the VM's specific private
LAN IP (not `0.0.0.0`), set `JOBPARSER_NOVNC_URL` to the resulting protected noVNC
address, and run `up -d` again. This publishes both 8080 and 6080. **BasicAuth and
VNC over plain HTTP expose reusable credentials and browser content to anyone able
to observe that traffic.** Prefer a VPN plus
a TLS reverse proxy, or keep the SSH tunnel. A host TLS proxy can use the default
loopback backend; configure its own authentication/access restrictions and valid
certificates. Docker-published ports can bypass UFW rules: enforce restrictions
at Proxmox/upstream firewalls and Docker's supported firewall chains, and test
from an untrusted network. Do not forward router ports or expose this panel
directly to the Internet. Use current Docker Engine (28+ recommended for loopback
publishing isolation).

## Container Security

The image is `python:3.13-slim-bookworm` with `chromium` and `chromium-driver`
installed together from Debian's repositories. `CHROME_BINARY=/usr/bin/chromium`
and `CHROMEDRIVER_PATH=/usr/bin/chromedriver` bypass webdriver-manager downloads.
`JOBPARSER_HEADLESS=1` is the container default; the authenticated panel overrides
it only for an explicitly requested interactive task.
Outside the container, an unset driver path retains the desktop
webdriver-manager fallback. Boolean driver flags accept `1`, `true`, `yes`, `on`.
No user-agent spoofing or browser-evasion flags are used.

`JOBPARSER_CHROME_NO_SANDBOX=1` is enabled only in this container configuration.
It disables Chromium's sandbox, reducing protection against malicious pages.
Accept this tradeoff only for a private, isolated, non-root container in a
dedicated VM. Do not reuse the flag for normal desktop browsing. The container
uses `no-new-privileges`, normal bridge networking, `init: true`, 1 GB shared
memory, a 40-second shutdown grace period and `restart: unless-stopped`.
Never add privileged mode, host networking, the Docker socket, or unrelated
host/secret mounts. The allowlisted build context excludes `.env*`, CVs, databases,
reports, tests, Git history and virtual environments. Runtime credentials remain
visible to Docker administrators; restrict host access accordingly.

## Operations

Status and logs (review logs for sensitive content before sharing):

```sh
sudo docker compose --env-file .env.homelab ps
sudo docker compose --env-file .env.homelab logs --tail=100 jobparser
```

The image healthcheck requests only `/healthz` on internal port 8080, with no
credentials. It checks the panel, not external sites, Gemini or browser readiness.
Docker marks failures unhealthy; the restart policy does not itself restart an
unhealthy but still-running process. Keep the internal port fixed at 8080 unless
also updating the healthcheck and published-port mapping.

Before updates or backups, cancel/wait for active jobs. For a consistent backup
of SQLite databases and associated files, stop the service before copying:

```sh
sudo docker compose --env-file .env.homelab stop
sudo tar -czf "$HOME/jobparser-backup-$(date +%Y%m%d-%H%M%S).tgz" results cv_storage web_state
sudo docker compose --env-file .env.homelab start
```

Store backups off the VM, encrypted and access-restricted: they contain CVs and
job data. Keep `.env.homelab` separately in an encrypted secret backup. Do not
copy live SQLite files alone; an online backup requires SQLite's backup API for
each database and coordination with the application for cross-file consistency.
VM snapshots alone are not an off-host backup.

Restore a trusted backup into a clean repository deployment while stopped;
move any existing data directories aside first rather than merging old state:

```sh
sudo docker compose --env-file .env.homelab stop
# Only after moving aside existing results, cv_storage and web_state:
sudo tar -xzf /path/to/jobparser-backup.tgz -C .
sudo chown -R 1000:1000 results cv_storage web_state
sudo chmod 700 results cv_storage web_state
# Restore .env.homelab separately, with mode 600 and matching APP_UID/APP_GID.
sudo docker compose --env-file .env.homelab up -d
curl --fail http://127.0.0.1:8080/healthz
```

Update after a backup, from a clean checkout on your intended branch:

```sh
git pull --ff-only
sudo docker compose --env-file .env.homelab build --pull --no-cache
sudo docker compose --env-file .env.homelab up -d
sudo docker compose --env-file .env.homelab ps
```

The no-cache rebuild refreshes distro browser security packages as well as Python
dependencies; the browser and driver come from the same apt transaction. Preserve
the previous commit/image and matching backup if rollback is needed. Changing
UID/GID also requires rebuilding and adjusting ownership of all mounted files.

Exact standalone image commands (the first builds only; use Compose for normal
deployment so mounts, secrets, init and shared memory are configured):

```sh
sudo docker build --pull --build-arg APP_UID=1000 --build-arg APP_GID=1000 -t jobparser:homelab .
sudo docker run --rm --entrypoint sh jobparser:homelab -c 'id; chromium --version; chromedriver --version'
```

The version command does not start a browser session. The browser and driver
major versions should match. Never pass API keys or passwords as build arguments.
