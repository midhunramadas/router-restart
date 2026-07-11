"""
Airtel router reboot automation for Raspberry Pi Zero 2 W.
  - Fixed the double driver.quit() bug (was called in both except and finally)
  - Added low-memory Chromium flags to reduce crash rate on the Pi Zero 2 W
  - Added a pre-flight memory check that aborts early (with a clear log message)
    instead of letting Chromium OOM-crash mid-login
  - Wrapped each Selenium step with a small retry helper, since flaky waits are
    the most common failure mode under memory pressure
  - Handles the reboot confirmation as EITHER a DOM button OR a JS confirm()
    alert, since Airtel firmware varies on this and it's a common silent-failure
    point
  - driver.quit() now happens exactly once, in a single guaranteed cleanup path
  - Added console logging alongside the file log, so you can watch it live when
    testing over SSH
  - Small structural cleanup: functions instead of one long top-level script,
    so it's easier to unit-test or swap in the requests-based version later
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoAlertPresentException,
)

import time
import os
import sys
import subprocess
import platform
import logging
from datetime import datetime
from dotenv import load_dotenv

# ---------------- CONFIG ---------------- #

load_dotenv()

AIRTEL_ROUTER_IP = os.getenv("AIRTEL_ROUTER_IP")
USERNAME = os.getenv("AIRTEL_ROUTER_USERNAME")
PASSWORD = os.getenv("AIRTEL_ROUTER_PASSWORD")

if not AIRTEL_ROUTER_IP:
    raise ValueError("AIRTEL_ROUTER_IP not set in .env")
if not USERNAME or not PASSWORD:
    raise ValueError("Router credentials missing in .env")

LOG_FILE = "/var/log/router-reboot.log"
STAMP_FILE = "/tmp/router_reboot_log_reset"

WAIT_TIMEOUT = 10          # seconds, per-element wait
STEP_RETRIES = 2           # retries per Selenium step before giving up
MIN_FREE_MB = 80           # abort if free memory drops below this before launch
OFFLINE_POLL_INTERVAL = 5  # seconds
ONLINE_MAX_ATTEMPTS = 20   # 20 * 30s = 10 minutes
ONLINE_POLL_INTERVAL = 30  # seconds


# ---------------- LOGGING SETUP ---------------- #

def should_truncate() -> bool:
    """Ensure log truncation happens only once per week."""
    current_week = datetime.now().strftime("%Y-%U")
    try:
        if os.path.exists(STAMP_FILE):
            with open(STAMP_FILE, "r") as f:
                if f.read().strip() == current_week:
                    return False
        with open(STAMP_FILE, "w") as f:
            f.write(current_week)
        return True
    except OSError:
        return False


def setup_logging() -> None:
    if datetime.now().weekday() == 6 and should_truncate():
        try:
            with open(LOG_FILE, "w"):
                pass
        except PermissionError:
            print("Warning: no permission to truncate log file")

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Console output too, useful when running over SSH for testing
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)


# ---------------- UTIL ---------------- #

def ping(host: str) -> bool:
    param = "-n" if platform.system().lower() == "windows" else "-c"
    command = ["ping", param, "1", host]
    return subprocess.call(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ) == 0


def free_memory_mb() -> int:
    """Available memory in MB, using /proc/meminfo (Linux-only, fine for the Pi)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return -1  # unknown; don't block the run on a read failure


def retry_step(description: str, action, retries: int = STEP_RETRIES):
    """Run a Selenium step, retrying on timeout/stale-element style failures."""
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            return action()
        except (TimeoutException, WebDriverException) as e:
            last_exc = e
            logging.warning(f"{description} failed (attempt {attempt}): {e}")
            time.sleep(2)
    raise RuntimeError(f"{description} failed after {retries + 1} attempts") from last_exc


# ---------------- SELENIUM FLOW ---------------- #

def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.binary_location = "/usr/bin/chromium"

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")   # smaller than 1920x1080, less RAM
    options.add_argument("--disable-notifications")

    # Low-memory-specific flags for the Pi Zero 2 W (512MB RAM)
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("--no-first-run")
    options.add_argument("--js-flags=--max-old-space-size=128")
    # NOTE: --single-process was removed - it crashes outright on many recent
    # Chromium builds ("Chrome instance exited" at session creation) rather
    # than actually saving memory. --disable-dev-shm-usage + the flags above
    # are the safe way to keep the footprint down.

    service = Service(
        "/usr/bin/chromedriver",
        log_output="/tmp/chromedriver.log",
        service_args=["--verbose"],
    )
    return webdriver.Chrome(service=service, options=options)


def do_login(driver) -> None:
    driver.get(f"http://{AIRTEL_ROUTER_IP}/")

    username_field = retry_step(
        "locate username field",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.NAME, "Frm_Username"))
        ),
    )
    password_field = driver.find_element(By.NAME, "Frm_Password")
    username_field.send_keys(USERNAME)
    password_field.send_keys(PASSWORD)

    login_button = retry_step(
        "locate login button",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "LoginId"))
        ),
    )
    login_button.click()

    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.ID, "mgrAndDiag"))
    )


def do_reboot(driver) -> None:
    retry_step(
        "click mgrAndDiag",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "mgrAndDiag"))
        ).click(),
    )

    retry_step(
        "click devMgr",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "devMgr"))
        ).click(),
    )

    retry_step(
        "click Btn_restart",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "Btn_restart"))
        ).click(),
    )

    confirm_reboot(driver)


def confirm_reboot(driver) -> None:
    """
    Some Airtel firmware versions confirm the reboot via a DOM button
    (#confirmOK); others use a native JS confirm() dialog. Try the DOM
    button first, and fall back to a JS alert if it's not there.
    """
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "confirmOK"))
        ).click()
        logging.info("Confirmed reboot via DOM button")
        return
    except TimeoutException:
        pass

    try:
        WebDriverWait(driver, 3).until(EC.alert_is_present())
        driver.switch_to.alert.accept()
        logging.info("Confirmed reboot via JS alert")
        return
    except (TimeoutException, NoAlertPresentException):
        pass

    raise RuntimeError("Could not find a reboot confirmation dialog (neither DOM button nor JS alert)")


def run_selenium_flow() -> None:
    driver = None
    try:
        driver = build_driver()
        do_login(driver)
        do_reboot(driver)
        logging.info("Reboot command sent")
    finally:
        # Single, guaranteed cleanup path - fixes the old double driver.quit() bug
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException as e:
                logging.warning(f"driver.quit() raised during cleanup (safe to ignore): {e}")


# ---------------- REBOOT MONITOR ---------------- #

def wait_for_offline() -> None:
    logging.info("Waiting for router to go offline...")
    while ping(AIRTEL_ROUTER_IP):
        time.sleep(OFFLINE_POLL_INTERVAL)
    logging.info("Router is offline, waiting to come back online...")


def wait_for_online() -> bool:
    for attempt in range(1, ONLINE_MAX_ATTEMPTS + 1):
        time.sleep(ONLINE_POLL_INTERVAL)
        if ping(AIRTEL_ROUTER_IP):
            logging.info(f"Router is back online after {attempt * ONLINE_POLL_INTERVAL} seconds")
            return True
        logging.warning(f"Attempt {attempt}/{ONLINE_MAX_ATTEMPTS}... still down")
    logging.error("Router did not come back online")
    return False


# ---------------- MAIN ---------------- #

def main() -> int:
    setup_logging()

    if not ping(AIRTEL_ROUTER_IP):
        logging.error(f"Router not reachable at {AIRTEL_ROUTER_IP}")
        return 1

    mem = free_memory_mb()
    if mem != -1 and mem < MIN_FREE_MB:
        logging.error(f"Only {mem}MB free, skipping this run to avoid an OOM crash mid-reboot")
        return 1
    if mem != -1:
        logging.info(f"Free memory before launch: {mem}MB")

    logging.info("Router reboot initiated")

    try:
        run_selenium_flow()
    except Exception as e:
        logging.error(f"Failure during Selenium operation: {e}")
        return 1

    wait_for_offline()
    success = wait_for_online()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
