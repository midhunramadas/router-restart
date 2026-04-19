Airtel Router Auto-Reboot (Raspberry Pi)
----------------------------------------

Automate rebooting your Airtel router using a Raspberry Pi and Selenium (Chromium).
This script logs into the router’s web UI, triggers a reboot, and monitors when it comes back online.

✨ Features
------------
🔄 Automated router reboot via web UI

🧠 Smart reboot detection (offline → online)

🪵 Persistent logging to /var/log/router-reboot.log

🧹 Weekly log truncation (runs every Sunday, once per week)

⚙️ Environment-based configuration (.env)

🧪 Works on Raspberry Pi OS (ARM / aarch64)

📂 Project Structure
--------------------
router-restart/

├── router-restart.py

├── .env

└── README.md

⚙️ Requirements
---------------
System packages (Raspberry Pi OS)

Install Chromium and Chromedriver:
---------------------------------

sudo apt update

sudo apt install chromium chromium-driver

Verify installation:
-------------------
which chromium

which chromedriver

Expected output (example):

/usr/bin/chromium

/usr/bin/chromedriver

Python dependencies
--------------------
Create a virtual environment (recommended):

python3 -m venv venv

source venv/bin/activate

Install required packages:
-------------------------
pip install selenium python-dotenv

🔐 Configuration
----------------
Create a .env file in the project directory:
------------------------------------------
AIRTEL_ROUTER_IP=10.1.1.1

AIRTEL_ROUTER_USERNAME=admin

AIRTEL_ROUTER_PASSWORD=your_password

▶️ Usage
---
Run the script:

python3 router-restart.py

🪵 Logging
--------------
Logs are written to:

/var/log/router-reboot.log

Important
--
Writing to /var/log requires elevated permissions.

Option 1: Run with sudo

sudo python3 router-restart.py

Option 2: Grant permissions

sudo touch /var/log/router-reboot.log

sudo chown pihole:pihole /var/log/router-reboot.log

🧹 Log Management
--
Logs are automatically truncated once per week (Sunday)
Uses a stamp file to ensure truncation happens only once per week

⏱️ Automate with Cron
---------------------
To run daily at 3 AM:

crontab -e

Add:

0 3 * * * /home/pihole/router-restart/venv/bin/python /home/pihole/router-restart/router-restart.py

⚠️ Use absolute paths when running via cron

⚠️ Notes
---------
- This script uses Selenium because Airtel routers typically don’t expose a public reboot API
- UI changes in router firmware may break element selectors
- Tested on Raspberry Pi OS (ARM64)

🛠️ Troubleshooting
------------------
❌ AIRTEL_ROUTER_IP not set in .env

Ensure .env is in the same directory as the script

Ensure variable names match exactly

Avoid spaces:
------------
AIRTEL_ROUTER_IP=10.1.1.1  ✅

AIRTEL_ROUTER_IP = 10.1.1.1 ❌

❌ Selenium / Chromium errors

Ensure correct paths:
--
options.binary_location = "/usr/bin/chromium"

service = Service("/usr/bin/chromedriver")

❌ Permission denied for logs

Run with sudo or fix ownership of /var/log/router-reboot.log

📄 License
------------
GNU GENERAL PUBLIC LICENSE

🤝 Contributing
---------------
If your router model differs, feel free to contribute updated selectors.
