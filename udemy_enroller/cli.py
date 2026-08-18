"""CLI entrypoint for this script."""
import argparse
import logging
import platform
import sys
from argparse import Namespace
from importlib.metadata import PackageNotFoundError, distribution
from typing import Tuple, Union

from udemy_enroller import ALL_VALID_BROWSER_STRINGS, DriverManager, Settings
from udemy_enroller.logger import get_logger
from udemy_enroller.notifications import notify_free_courses
from udemy_enroller.runner import redeem_courses, redeem_courses_ui

logger = get_logger()


def enable_debug_logging() -> None:
    """
    Enable debug logging for the scripts.

    :return: None
    """
    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers:
        handler.setLevel(logging.DEBUG)
    logger.info("Enabled debug logging")


def log_package_details() -> None:
    """
    Log details of the package.

    :return: None
    """
    try:
        package_distribution = distribution("udemy_enroller")
        logger.debug(f"Name: {package_distribution.metadata['Name']}")
        logger.debug(f"Version: {package_distribution.version}")
        logger.debug(f"Location: {package_distribution.locate_file('')}")
    except PackageNotFoundError:
        logger.debug("Not installed on python env.")


def log_python_version():
    """
    Log version of python in use.

    :return: None
    """
    logger.debug(f"Python: {sys.version}")


def log_os_version():
    """
    Log version of the OS in use.

    :return: None
    """
    logger.debug(f"OS: {platform.platform()}")


def determine_if_scraper_enabled(
    idownloadcoupon_enabled: bool,
    freebiesglobal_enabled: bool,
    tutorialbar_enabled: bool,
    discudemy_enabled: bool,
    coursevania_enabled: bool,
) -> Tuple[bool, bool, bool, bool, bool]:
    """
    Determine what scrapers should be enabled and disabled.

    :return: tuple containing boolean of what scrapers should run
    """
    if (
        not idownloadcoupon_enabled
        and not freebiesglobal_enabled
        and not tutorialbar_enabled
        and not discudemy_enabled
        and not coursevania_enabled
    ):
        (
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
        ) = (True, True, True, True, True)

    return (
        idownloadcoupon_enabled,
        freebiesglobal_enabled,
        tutorialbar_enabled,
        discudemy_enabled,
        coursevania_enabled,
    )


def run(
    browser: str,
    idownloadcoupon_enabled: bool,
    freebiesglobal_enabled: bool,
    tutorialbar_enabled: bool,
    discudemy_enabled: bool,
    coursevania_enabled: bool,
    max_pages: Union[int, None],
    delete_settings: bool,
    delete_cookie: bool,
    notify_only: bool = False,
    notify_dry_run: bool = False,
):
    """
    Run the Udemy enroller or Telegram notification mode.

    Notification mode deliberately runs before Settings is created, so it does
    not ask for Udemy credentials and never attempts course enrolment.

    :return: None
    """
    if notify_only or notify_dry_run:
        if browser:
            logger.warning("--browser is ignored in notification mode")
        notify_free_courses(
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
            max_pages,
            dry_run=notify_dry_run,
        )
        return

    settings = Settings(delete_settings, delete_cookie)
    if browser:
        dm = DriverManager(browser=browser, is_ci_build=settings.is_ci_build)
        redeem_courses_ui(
            dm.driver,
            settings,
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
            max_pages,
        )
    else:
        redeem_courses(
            settings,
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
            max_pages,
        )


def parse_args() -> Namespace:
    """
    Parse args from the CLI or use the args passed in.

    :return: Args to be used in the script
    """
    parser = argparse.ArgumentParser(description="Udemy Enroller")

    parser.add_argument(
        "--browser",
        required=False,
        type=str,
        choices=ALL_VALID_BROWSER_STRINGS,
        help="Browser to use for Udemy Enroller",
    )
    parser.add_argument(
        "--idownloadcoupon",
        action="store_true",
        default=False,
        help="Run idownloadcoupon scraper",
    )
    parser.add_argument(
        "--freebiesglobal",
        action="store_true",
        default=False,
        help="Run freebiesglobal scraper",
    )
    parser.add_argument(
        "--tutorialbar",
        action="store_true",
        default=False,
        help="Run tutorialbar scraper",
    )
    parser.add_argument(
        "--discudemy",
        action="store_true",
        default=False,
        help="Run discudemy scraper",
    )
    parser.add_argument(
        "--coursevania",
        action="store_true",
        default=False,
        help="Run coursevania scraper",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Max pages to scrape from sites (if pagination exists) (Default is 5)",
    )
    parser.add_argument(
        "--delete-settings",
        action="store_true",
        default=False,
        help="Delete any existing settings file",
    )
    parser.add_argument(
        "--delete-cookie",
        action="store_true",
        default=False,
        help="Delete existing cookie file",
    )
    parser.add_argument(
        "--notify-only",
        action="store_true",
        default=False,
        help=(
            "Find paid Udemy courses temporarily reduced to zero and send "
            "Telegram notifications without logging into Udemy"
        ),
    )
    parser.add_argument(
        "--notify-dry-run",
        action="store_true",
        default=False,
        help=(
            "Run notification discovery and filtering without Telegram delivery "
            "or updating notification history"
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def main():
    """Entrypoint for scripts."""
    args = parse_args()
    if args:
        if args.debug:
            enable_debug_logging()
            log_package_details()
            log_python_version()
            log_os_version()
        (
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
        ) = determine_if_scraper_enabled(
            args.idownloadcoupon,
            args.freebiesglobal,
            args.tutorialbar,
            args.discudemy,
            args.coursevania,
        )
        run(
            args.browser,
            idownloadcoupon_enabled,
            freebiesglobal_enabled,
            tutorialbar_enabled,
            discudemy_enabled,
            coursevania_enabled,
            args.max_pages,
            args.delete_settings,
            args.delete_cookie,
            args.notify_only,
            args.notify_dry_run,
        )
