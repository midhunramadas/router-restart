# Airtel Router Auto-Reboot (Raspberry Pi)

Automate an Airtel-router reboot from a Raspberry Pi using Selenium and headless Chromium. The script signs in to the router UI, submits the reboot command, verifies that the router goes offline, then waits until both ping and the router's HTTP management service are available again.

## Features

- Automated reboot through the router web UI
- Reboot monitoring: detects offline state, then verifies ping **and** HTTP management UI recovery
- Bounded monitoring: offline detection times out after 2 minutes; online recovery after 10 minutes
- Low-memory Chromium settings suitable for a Pi Zero 2 W
- Rotating logs: up to 1 MB per file, with four retained backups
- Console-only fallback when the configured log file is not writable
- Environment-based configuration via `.env`
- Prevents overlapping scheduled runs with an exclusive lock at `/tmp/router-reboot.lock`
- Configurable Chromium and ChromeDriver paths

## Project structure

```text
router-restart/
├── router-restart.py
├── .env
└── README.md
```

## Requirements

### System packages (Raspberry Pi OS)

```bash
sudo apt update
sudo apt install chromium chromium-driver
```

Verify the installed locations:

```bash
which chromium
which chromedriver
```

Typical output:

```text
/usr/bin/chromium
/usr/bin/chromedriver
```

### Python dependencies

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install selenium python-dotenv
```

## Configuration

Create `.env` in the project directory:

```dotenv
AIRTEL_ROUTER_IP=10.1.1.1
AIRTEL_ROUTER_USERNAME=admin
AIRTEL_ROUTER_PASSWORD=your_password
```

Optional settings, with their defaults:

```dotenv
ROUTER_REBOOT_LOG_FILE=/var/log/router-reboot.log
CHROMIUM_BINARY=/usr/bin/chromium
CHROMEDRIVER_BINARY=/usr/bin/chromedriver
```

Avoid spaces around `=` in `.env` values.

## Usage

```bash
python3 router-restart.py
```

The script exits non-zero if configuration is invalid, the router cannot be reached, Selenium fails, the router never goes offline, or it does not return within the recovery window.

## Logging

The default log file is:

```text
/var/log/router-reboot.log
```

Logs rotate at 1 MB and retain four backups (`router-reboot.log.1` through `.4`). If the process cannot write this path, it continues with console logging and emits a warning.

For persistent file logging without `sudo`, set a writable path in `.env`, for example:

```dotenv
ROUTER_REBOOT_LOG_FILE=/home/pihole/router-reboot.log
```

Alternatively, create the default file and grant the account ownership:

```bash
sudo touch /var/log/router-reboot.log
sudo chown pihole:pihole /var/log/router-reboot.log
```

## Schedule with cron

To run daily at 3 AM:

```bash
crontab -e
```

Add the following entry, replacing the paths with yours:

```cron
0 3 * * * /home/pihole/router-restart/venv/bin/python /home/pihole/router-restart/router-restart.py
```

Use absolute paths with cron. The script skips a run if another instance is still active, which prevents concurrent router sessions.

## Troubleshooting

### `AIRTEL_ROUTER_IP not set in .env`

Confirm `.env` is in the project directory and contains all three required variables exactly as shown above.

### Selenium or Chromium startup errors

Check the installed binary paths:

```bash
which chromium
which chromedriver
```

If they differ from the defaults, set `CHROMIUM_BINARY` and `CHROMEDRIVER_BINARY` in `.env`.

### The router does not appear to recover

The script requires both ICMP ping and the router's HTTP management page to respond. Confirm that the configured router IP is correct and that ICMP is enabled on the router.

### Another reboot run is already in progress

Wait for the earlier execution to finish. The lock is released automatically when the process exits.

## Notes

- Airtel router firmware updates can change UI element IDs and break Selenium selectors.
- The current settings target Raspberry Pi OS and Chromium on ARM/aarch64 hardware.
- Selenium is used because these routers generally do not provide a public reboot API.

## License

GNU General Public License

## Contributing

If your router model uses different selectors or a different reboot flow, contributions are welcome.
