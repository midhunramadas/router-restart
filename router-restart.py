"""
Airtel router reboot automation.
  - Fixed the double driver.quit() bug (was called in both except and finally)
  - Added low-memory Chromium flags to reduce crash rate on the Pi Zero 2 W
  - Added a pre-flight memory check that aborts early (with a clear log message)
    instead of letting Chromium OOM-crash mid-login
  - Retries element waits without replaying state-changing clicks
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
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
)

import time
import os
import sys
import subprocess
import platform
import logging
import fcntl
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from dotenv import load_dotenv

# ---------------- CONFIG ---------------- #

@dataclass(frozen=True)
class Config:
    router_ip: str
    username: str
    password: str
    log_file: str
    chromium_binary: str
    chromedriver_binary: str


def load_config() -> Config:
    """Load configuration at run time so importing this module remains testable."""
    load_dotenv()
    router_ip = os.getenv("AIRTEL_ROUTER_IP")
    username = os.getenv("AIRTEL_ROUTER_USERNAME")
    password = os.getenv("AIRTEL_ROUTER_PASSWORD")
    if not router_ip:
        raise ValueError("AIRTEL_ROUTER_IP not set in .env")
    if not username or not password:
        raise ValueError("Router credentials missing in .env")
    return Config(
        router_ip=router_ip,
        username=username,
        password=password,
        log_file=os.getenv("ROUTER_REBOOT_LOG_FILE", "/var/log/router-reboot.log"),
        chromium_binary=os.getenv("CHROMIUM_BINARY", "/usr/bin/chromium"),
        chromedriver_binary=os.getenv("CHROMEDRIVER_BINARY", "/usr/bin/chromedriver"),
    )

WAIT_TIMEOUT = 10          # seconds, per-element wait
STEP_RETRIES = 2           # retries per Selenium step before giving up
SELENIUM_MAX_ATTEMPTS = 3  # full browser/login/reboot attempts before failing
SELENIUM_RETRY_DELAY = 5   # seconds between full Selenium attempts
MIN_FREE_MB = 80           # abort if free memory drops below this before launch
OFFLINE_POLL_INTERVAL = 5  # seconds
OFFLINE_MAX_ATTEMPTS = 24  # 24 * 5s = 2 minutes
ONLINE_MAX_ATTEMPTS = 20   # 20 * 30s = 10 minutes
ONLINE_POLL_INTERVAL = 30  # seconds
LOCK_FILE = "/tmp/router-reboot.lock"
LOGGER = logging.getLogger("router_reboot")


# ---------------- LOGGING SETUP ---------------- #

def setup_logging(log_file: str) -> None:
    """Configure console logging and rotate the file log when it is writable."""
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    LOGGER.addHandler(console_handler)
    try:
        file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=4)
        file_handler.setFormatter(fmt)
        LOGGER.addHandler(file_handler)
    except OSError as exc:
        LOGGER.warning("Cannot write log file %s; using console only: %s", log_file, exc)


# ---------------- UTIL ---------------- #

def ping(host: str) -> bool:
    param = "-n" if platform.system().lower() == "windows" else "-c"
    command = ["ping", param, "1", host]
    try:
        return subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def router_web_ready(router_ip: str) -> bool:
    """Return whether the router's management HTTP service is responding."""
    request = Request(f"http://{router_ip}/", method="GET")
    try:
        with urlopen(request, timeout=3):
            return True
    except HTTPError:
        # Authentication errors still prove that the management service is up.
        return True
    except (URLError, OSError):
        return False


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


def retry_wait(description: str, action, retries: int = STEP_RETRIES):
    """Retry only idempotent element waits; callers perform clicks once."""
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            return action()
        except (TimeoutException, StaleElementReferenceException) as e:
            last_exc = e
            LOGGER.warning("%s failed (attempt %s): %s", description, attempt, e)
            time.sleep(2)
    raise RuntimeError(f"{description} failed after {retries + 1} attempts") from last_exc


# ---------------- SELENIUM FLOW ---------------- #

def build_driver(config: Config) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.binary_location = config.chromium_binary

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
        config.chromedriver_binary,
        log_output="/tmp/chromedriver.log",
        service_args=["--verbose"],
    )
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(WAIT_TIMEOUT)
    driver.set_script_timeout(WAIT_TIMEOUT)
    return driver


def do_login(driver, config: Config) -> None:
    driver.get(f"http://{config.router_ip}/")

    username_field = retry_wait(
        "locate username field",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.NAME, "Frm_Username"))
        ),
    )
    password_field = retry_wait(
        "locate password field",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.NAME, "Frm_Password"))
        ),
    )
    username_field.send_keys(config.username)
    password_field.send_keys(config.password)

    login_button = retry_wait(
        "locate login button",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "LoginId"))
        ),
    )
    login_button.click()

    retry_wait(
        "wait for authenticated page",
        lambda: WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "mgrAndDiag"))
        ),
    )


def do_reboot(driver) -> None:
    for description, element_id in (
        ("mgrAndDiag", "mgrAndDiag"),
        ("devMgr", "devMgr"),
        ("Btn_restart", "Btn_restart"),
    ):
        button = retry_wait(
            f"locate {description}",
            lambda element_id=element_id: WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, element_id))
            ),
        )
        button.click()

    confirm_reboot(driver)


def confirm_reboot(driver) -> None:
    """
    Some Airtel firmware versions confirm via a DOM button (#confirmOK), while
    others use a native JS confirm() dialog. Check the alert first because it
    blocks all DOM interactions while present.
    """
    if accept_alert(driver, 1):
        return

    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.ID, "confirmOK"))
        ).click()
        LOGGER.info("Confirmed reboot via DOM button")
        return
    except (TimeoutException, UnexpectedAlertPresentException):
        if accept_alert(driver, 3):
            return
    raise RuntimeError("Could not find a reboot confirmation dialog (neither DOM button nor JS alert)")


def accept_alert(driver, timeout: int) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        driver.switch_to.alert.accept()
        LOGGER.info("Confirmed reboot via JS alert")
        return True
    except (TimeoutException, NoAlertPresentException):
        return False


def run_selenium_flow(config: Config) -> None:
    driver = None
    try:
        driver = build_driver(config)
        do_login(driver, config)
        do_reboot(driver)
        LOGGER.info("Reboot command sent")
    finally:
        # Single, guaranteed cleanup path - fixes the old double driver.quit() bug
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException as e:
                LOGGER.warning("driver.quit() raised during cleanup (safe to ignore): %s", e)


# ---------------- REBOOT MONITOR ---------------- #

def wait_for_offline(router_ip: str) -> bool:
    LOGGER.info("Waiting for router to go offline...")
    for _ in range(OFFLINE_MAX_ATTEMPTS):
        if not ping(router_ip) or not router_web_ready(router_ip):
            LOGGER.info("Router is offline, waiting to come back online...")
            return True
        time.sleep(OFFLINE_POLL_INTERVAL)
    LOGGER.error("Router did not go offline within %s seconds", OFFLINE_MAX_ATTEMPTS * OFFLINE_POLL_INTERVAL)
    return False


def wait_for_online(router_ip: str) -> bool:
    for attempt in range(1, ONLINE_MAX_ATTEMPTS + 1):
        time.sleep(ONLINE_POLL_INTERVAL)
        if ping(router_ip) and router_web_ready(router_ip):
            LOGGER.info("Router is back online after %s seconds", attempt * ONLINE_POLL_INTERVAL)
            return True
        LOGGER.warning("Attempt %s/%s... still down", attempt, ONLINE_MAX_ATTEMPTS)
    LOGGER.error("Router did not come back online")
    return False


# ---------------- MAIN ---------------- #

def main() -> int:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_file)

    lock_handle = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        LOGGER.error("Another router reboot run is already in progress")
        lock_handle.close()
        return 1

    try:
        if not ping(config.router_ip):
            LOGGER.error("Router not reachable at %s", config.router_ip)
            return 1

        mem = free_memory_mb()
        if mem != -1 and mem < MIN_FREE_MB:
            LOGGER.error("Only %sMB free, skipping this run to avoid an OOM crash mid-reboot", mem)
            return 1
        if mem != -1:
            LOGGER.info("Free memory before launch: %sMB", mem)

        LOGGER.info("Router reboot initiated")

        for attempt in range(1, SELENIUM_MAX_ATTEMPTS + 1):
            try:
                run_selenium_flow(config)
                break
            except Exception:
                LOGGER.exception(
                    "Selenium operation failed (attempt %s/%s)",
                    attempt,
                    SELENIUM_MAX_ATTEMPTS,
                )
                if attempt == SELENIUM_MAX_ATTEMPTS:
                    return 1
                LOGGER.info("Retrying Selenium operation in %s seconds", SELENIUM_RETRY_DELAY)
                time.sleep(SELENIUM_RETRY_DELAY)

        if not wait_for_offline(config.router_ip):
            return 1
        return 0 if wait_for_online(config.router_ip) else 1
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


if __name__ == "__main__":
    sys.exit(main())
