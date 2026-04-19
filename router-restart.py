from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

import time
import os
from dotenv import load_dotenv
from datetime import datetime
import subprocess
import platform
import logging

# Load environment variables
load_dotenv()

AIRTEL_ROUTER_IP = os.getenv("AIRTEL_ROUTER_IP")
USERNAME = os.getenv("AIRTEL_ROUTER_USERNAME")
PASSWORD = os.getenv("AIRTEL_ROUTER_PASSWORD")

# Validate config
if not AIRTEL_ROUTER_IP:
    raise ValueError("AIRTEL_ROUTER_IP not set in .env")

if not USERNAME or not PASSWORD:
    raise ValueError("Router credentials missing in .env")


# ---------------- LOGGING SETUP ---------------- #

LOG_FILE = "/var/log/router-reboot.log"
STAMP_FILE = "/tmp/router_reboot_log_reset"

def should_truncate():
    """Ensure log truncation happens only once per week"""
    current_week = datetime.now().strftime("%Y-%U")

    try:
        if os.path.exists(STAMP_FILE):
            with open(STAMP_FILE, "r") as f:
                if f.read().strip() == current_week:
                    return False

        with open(STAMP_FILE, "w") as f:
            f.write(current_week)

        return True
    except:
        return False


def setup_logging():
    # Truncate only on Sunday (weekday=6) and only once per week
    if datetime.now().weekday() == 6 and should_truncate():
        try:
            with open(LOG_FILE, "w"):
                pass
        except PermissionError:
            print("⚠️ No permission to truncate log file")

    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )


setup_logging()

# ---------------- UTIL ---------------- #

def ping(host):
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '1', host]
    return subprocess.call(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    ) == 0


# ---------------- MAIN FLOW ---------------- #

# Check router availability
if not ping(AIRTEL_ROUTER_IP):
    logging.error(f"Router not reachable at {AIRTEL_ROUTER_IP}")
    exit(1)

logging.info("Router reboot initiated")


# Selenium setup (Chromium on Raspberry Pi)
options = webdriver.ChromeOptions()
options.binary_location = "/usr/bin/chromium"

options.add_argument('--headless=new')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--disable-gpu')
options.add_argument('--window-size=1920,1080')
options.add_argument('--disable-notifications')

service = Service("/usr/bin/chromedriver")

driver = None

try:
    driver = webdriver.Chrome(service=service, options=options)

    # Open router page
    driver.get(f"http://{AIRTEL_ROUTER_IP}/")
    time.sleep(3)

    # Login
    username_field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "Frm_Username"))
    )
    password_field = driver.find_element(By.NAME, "Frm_Password")

    username_field.send_keys(USERNAME)
    password_field.send_keys(PASSWORD)

    login_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "LoginId"))
    )
    login_button.click()

    time.sleep(5)

    # Navigate and reboot
    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "mgrAndDiag"))
    ).click()

    time.sleep(3)

    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "devMgr"))
    ).click()

    time.sleep(3)

    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "Btn_restart"))
    ).click()

    time.sleep(3)

    WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.ID, "confirmOK"))
    ).click()

    logging.info("Reboot command sent")

except Exception as e:
    logging.error(f"Failure during Selenium operation: {e}")
    if driver:
        driver.quit()
    raise

finally:
    if driver:
        driver.quit()


# ---------------- REBOOT MONITOR ---------------- #

logging.info("Waiting for router to go offline...")

# Wait until router goes offline
while ping(AIRTEL_ROUTER_IP):
    time.sleep(5)

logging.info("Router is offline, waiting to come back online...")

attempts = 0
max_attempts = 20  # 10 minutes

while attempts < max_attempts:
    time.sleep(30)
    attempts += 1

    if ping(AIRTEL_ROUTER_IP):
        logging.info(f"Router is back online after {attempts * 30} seconds")
        break
    else:
        logging.warning(f"Attempt {attempts}/{max_attempts}... still down")

if attempts == max_attempts:
    logging.error("Router did not come back online")

