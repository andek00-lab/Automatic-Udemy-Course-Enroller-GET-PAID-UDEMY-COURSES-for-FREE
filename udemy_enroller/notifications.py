"""Telegram notification mode for free Udemy coupon offers."""
import asyncio
import html
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from udemy_enroller.logger import get_logger
from udemy_enroller.scrapers.manager import ScraperManager
from udemy_enroller.utils import get_app_dir

logger = get_logger()


def _split_csv(value: str) -> Tuple[str, ...]:
    """Split a comma separated environment setting."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment with a safe fallback."""
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, value, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment with a safe fallback."""
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, value, default)
        return default


def _as_float(value) -> Optional[float]:
    """Convert a pricing value to float when possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class NotificationConfig:
    """Runtime configuration for the Telegram notification mode."""

    bot_token: Optional[str]
    chat_id: Optional[str]
    languages: Tuple[str, ...] = ()
    categories: Tuple[str, ...] = ()
    include_keywords: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()
    min_rating: float = 0.0
    min_students: int = 0
    memory_days: int = 60
    hit_min_rating: float = 4.5
    hit_min_students: int = 1000
    max_hit_messages: int = 5
    request_timeout: int = 20
    state_file: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        """Build configuration from environment variables."""
        state_file = os.environ.get("UDEMY_NOTIFY_STATE_FILE")
        if not state_file:
            state_file = os.path.join(get_app_dir(), "notified_courses.json")

        return cls(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            languages=_split_csv(os.environ.get("UDEMY_NOTIFY_LANGUAGES", "")),
            categories=_split_csv(os.environ.get("UDEMY_NOTIFY_CATEGORIES", "")),
            include_keywords=_split_csv(
                os.environ.get("UDEMY_NOTIFY_INCLUDE_KEYWORDS", "")
            ),
            exclude_keywords=_split_csv(
                os.environ.get("UDEMY_NOTIFY_EXCLUDE_KEYWORDS", "")
            ),
            min_rating=_env_float("UDEMY_NOTIFY_MIN_RATING", 0.0),
            min_students=_env_int("UDEMY_NOTIFY_MIN_STUDENTS", 0),
            memory_days=max(1, _env_int("UDEMY_NOTIFY_MEMORY_DAYS", 60)),
            hit_min_rating=_env_float("UDEMY_NOTIFY_HIT_MIN_RATING", 4.5),
            hit_min_students=_env_int("UDEMY_NOTIFY_HIT_MIN_STUDENTS", 1000),
            max_hit_messages=max(0, _env_int("UDEMY_NOTIFY_MAX_HITS", 5)),
            request_timeout=max(5, _env_int("UDEMY_NOTIFY_TIMEOUT", 20)),
            state_file=state_file,
        )


@dataclass(frozen=True)
class CourseOffer:
    """Validated paid Udemy course temporarily reduced to zero."""

    course_id: int
    title: str
    coupon_code: str
    url: str
    language: str
    category: str
    subcategory: str
    rating: float
    students: int
    duration: str
    instructor: str
    regular_price: Optional[float] = None

    @property
    def searchable_text(self) -> str:
        """Return the fields used by keyword filters."""
        return " ".join(
            (
                self.title,
                self.category,
                self.subcategory,
                self.instructor,
            )
        ).casefold()


class NotificationState:
    """Persist recently notified course IDs to avoid duplicate alerts."""

    def __init__(self, path: str, memory_days: int):
        self.path = path
        self.memory_days = memory_days
        self._courses = self._load()
        self._purge_expired()

    def _load(self) -> Dict[str, Dict[str, str]]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as state_file:
                payload = json.load(state_file)
            courses = payload.get("courses", {})
            return courses if isinstance(courses, dict) else {}
        except (OSError, ValueError, TypeError):
            logger.warning("Could not read notification state; starting fresh")
            return {}

    def _purge_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.memory_days)
        retained = {}
        for course_id, data in self._courses.items():
            try:
                notified_at = datetime.fromisoformat(data["notified_at"])
                if notified_at.tzinfo is None:
                    notified_at = notified_at.replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            if notified_at >= cutoff:
                retained[course_id] = data
        self._courses = retained

    def should_notify(self, course_id: int) -> bool:
        """Return True when a course is not inside the memory window."""
        return str(course_id) not in self._courses

    def mark(self, offer: CourseOffer) -> None:
        """Record a successfully delivered offer."""
        self._courses[str(offer.course_id)] = {
            "notified_at": datetime.now(timezone.utc).isoformat(),
            "coupon_code": offer.coupon_code,
            "title": offer.title,
        }

    def save(self) -> None:
        """Atomically save state to disk."""
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = self.path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump(
                {"courses": self._courses},
                state_file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        os.replace(temp_path, self.path)


class UdemyOfferInspector:
    """Validate scraper links and enrich them with public Udemy metadata."""

    BASE_URL = "https://www.udemy.com"
    COURSE_DETAILS = BASE_URL + "/api-2.0/courses/{}/"
    COUPON_DETAILS = BASE_URL + "/api-2.0/course-landing-components/{}/me/"
    UDEMY_HEADERS = {
        "User-Agent": "okhttp/4.9.2 UdemyAndroid 8.9.2(499) (phone)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.udemy.com/",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self.UDEMY_HEADERS)

    @staticmethod
    def _parse_coupon_link(course_link: str) -> Tuple[str, str]:
        parsed = urlparse(course_link)
        query = parse_qs(parsed.query)
        coupon_values = query.get("couponCode") or query.get("couponcode")
        if not coupon_values or not coupon_values[0]:
            raise ValueError("Coupon code missing from URL")

        clean_query = {
            key: values
            for key, values in query.items()
            if key.casefold() != "couponcode"
        }
        flattened_query = []
        for key, values in clean_query.items():
            for value in values:
                flattened_query.append((key, value))
        clean_url = urlunparse(parsed._replace(query=urlencode(flattened_query)))
        return clean_url, coupon_values[0]

    def _get_course_id(self, course_url: str) -> int:
        response = self.session.get(course_url, timeout=self.timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        body = soup.find("body")
        if body is None or not body.get("data-clp-course-id"):
            raise ValueError("Could not determine Udemy course ID")
        return int(body["data-clp-course-id"])

    def _coupon_pricing(self, course_id: int, coupon_code: str) -> Dict:
        response = self.session.get(
            self.COUPON_DETAILS.format(course_id),
            params={
                "couponCode": coupon_code,
                "components": "price_text,deal_badge,discount_expiration",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _course_details(self, course_id: int) -> Dict:
        response = self.session.get(
            self.COURSE_DETAILS.format(course_id),
            params={
                "fields[course]": (
                    "title,primary_category,primary_subcategory,avg_rating_recent,"
                    "visible_instructors,locale,estimated_content_length,"
                    "num_subscribers"
                )
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def inspect(self, course_link: str) -> Optional[CourseOffer]:
        """Return an offer only when a paid course is currently free by coupon."""
        course_url, coupon_code = self._parse_coupon_link(course_link)
        course_id = self._get_course_id(course_url)

        pricing = self._coupon_pricing(course_id, coupon_code)
        pricing_result = (
            pricing.get("price_text", {})
            .get("data", {})
            .get("pricing_result", {})
        )
        current_price = _as_float(pricing_result.get("price", {}).get("amount"))
        regular_price = _as_float(
            pricing_result.get("list_price", {}).get("amount")
        )

        if current_price is None or current_price != 0:
            return None
        if regular_price is None or regular_price <= 0:
            return None

        details = self._course_details(course_id)
        instructors = details.get("visible_instructors") or []
        instructor = ", ".join(
            item.get("title", "") for item in instructors if item.get("title")
        )

        locale = details.get("locale") or {}
        category = details.get("primary_category") or {}
        subcategory = details.get("primary_subcategory") or {}

        return CourseOffer(
            course_id=course_id,
            title=details.get("title") or course_url,
            coupon_code=coupon_code,
            url=course_link,
            language=locale.get("simple_english_title") or "",
            category=category.get("title") or "",
            subcategory=subcategory.get("title") or "",
            rating=float(details.get("avg_rating_recent") or 0.0),
            students=int(details.get("num_subscribers") or 0),
            duration=str(details.get("estimated_content_length") or ""),
            instructor=instructor,
            regular_price=regular_price,
        )


def offer_matches(offer: CourseOffer, config: NotificationConfig) -> bool:
    """Apply user-configurable language, category and quality filters."""
    if config.languages:
        wanted = {item.casefold() for item in config.languages}
        if offer.language.casefold() not in wanted:
            return False

    if config.categories:
        wanted = {item.casefold() for item in config.categories}
        course_categories = {
            offer.category.casefold(),
            offer.subcategory.casefold(),
        }
        if not wanted.intersection(course_categories):
            return False

    text = offer.searchable_text
    if config.include_keywords:
        if not any(keyword.casefold() in text for keyword in config.include_keywords):
            return False

    if config.exclude_keywords:
        if any(keyword.casefold() in text for keyword in config.exclude_keywords):
            return False

    if offer.rating < config.min_rating:
        return False

    if offer.students < config.min_students:
        return False

    return True


def is_hit(offer: CourseOffer, config: NotificationConfig) -> bool:
    """Return True for high-quality offers worth an immediate alert."""
    return (
        offer.rating >= config.hit_min_rating
        and offer.students >= config.hit_min_students
    )


class TelegramNotifier:
    """Small Telegram Bot API client."""

    API_URL = "https://api.telegram.org/bot{}/sendMessage"

    def __init__(self, token: str, chat_id: str, timeout: int = 20):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> bool:
        """Send one HTML-formatted Telegram message."""
        try:
            response = requests.post(
                self.API_URL.format(self.token),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                logger.error("Telegram rejected a notification")
                return False
            return True
        except (requests.RequestException, ValueError) as exc:
            logger.error("Telegram notification failed: %s", exc)
            return False


def _format_hit(offer: CourseOffer) -> str:
    title = html.escape(offer.title)
    language = html.escape(offer.language or "unknown")
    category = html.escape(
        " / ".join(item for item in (offer.category, offer.subcategory) if item)
        or "unknown"
    )
    duration = html.escape(offer.duration or "unknown")
    instructor = html.escape(offer.instructor or "unknown")
    url = html.escape(offer.url, quote=True)

    return (
        "🔥 <b>HIT: darmowy kurs Udemy</b>\n\n"
        f"<b>{title}</b>\n"
        f"⭐ {offer.rating:.2f}   👥 {offer.students:,}\n"
        f"🌐 {language}\n"
        f"📁 {category}\n"
        f"⏱ {duration}\n"
        f"👤 {instructor}\n\n"
        "💸 Kupon sprawdzony: <b>cena 0</b>\n"
        f'🔗 <a href="{url}">Odbierz kurs</a>'
    )


def _format_digest_item(offer: CourseOffer) -> str:
    title = html.escape(offer.title)
    url = html.escape(offer.url, quote=True)
    return (
        f'• <a href="{url}">{title}</a>\n'
        f"  ⭐ {offer.rating:.2f} · 👥 {offer.students:,} · "
        f"{html.escape(offer.language or 'unknown')}"
    )


def _digest_chunks(offers: Sequence[CourseOffer]) -> List[Tuple[str, List[CourseOffer]]]:
    """Split digest messages well below Telegram's 4096-character limit."""
    chunks = []
    header = "📚 <b>Nowe darmowe kursy Udemy</b>\n\n"
    current_text = header
    current_offers: List[CourseOffer] = []

    for offer in offers:
        item = _format_digest_item(offer)
        candidate = current_text + item + "\n\n"
        if current_offers and len(candidate) > 3500:
            chunks.append((current_text.rstrip(), current_offers))
            current_text = header + item + "\n\n"
            current_offers = [offer]
        else:
            current_text = candidate
            current_offers.append(offer)

    if current_offers:
        chunks.append((current_text.rstrip(), current_offers))
    return chunks


def _log_dry_run(offers: Sequence[CourseOffer], config: NotificationConfig) -> None:
    logger.info("Dry run: %s matching course(s)", len(offers))
    for offer in offers:
        prefix = "HIT" if is_hit(offer, config) else "COURSE"
        logger.info(
            "[%s] %s | rating=%.2f | students=%s | %s",
            prefix,
            offer.title,
            offer.rating,
            offer.students,
            offer.url,
        )


def notify_free_courses(
    idownloadcoupon_enabled: bool,
    freebiesglobal_enabled: bool,
    tutorialbar_enabled: bool,
    discudemy_enabled: bool,
    coursevania_enabled: bool,
    max_pages: Optional[int],
    dry_run: bool = False,
) -> None:
    """Scrape, validate, filter, de-duplicate and notify about free courses."""
    config = NotificationConfig.from_env()
    if not dry_run and (not config.bot_token or not config.chat_id):
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before using --notify-only"
        )

    scrapers = ScraperManager(
        idownloadcoupon_enabled,
        freebiesglobal_enabled,
        tutorialbar_enabled,
        discudemy_enabled,
        coursevania_enabled,
        max_pages,
    )

    loop = asyncio.get_event_loop()
    course_links = loop.run_until_complete(scrapers.run())
    unique_links = list(dict.fromkeys(course_links))
    logger.info("Notification mode found %s unique coupon link(s)", len(unique_links))

    state = NotificationState(config.state_file, config.memory_days)
    inspector = UdemyOfferInspector(config.request_timeout)
    offers_by_id: Dict[int, CourseOffer] = {}

    for course_link in unique_links:
        try:
            offer = inspector.inspect(course_link)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.debug("Skipping invalid course link: %s", exc)
            continue

        if offer is None:
            continue
        if offer.course_id in offers_by_id:
            continue
        if not offer_matches(offer, config):
            continue
        if not state.should_notify(offer.course_id):
            continue
        offers_by_id[offer.course_id] = offer

    offers = sorted(
        offers_by_id.values(),
        key=lambda item: (
            is_hit(item, config),
            item.rating,
            item.students,
        ),
        reverse=True,
    )

    if dry_run:
        _log_dry_run(offers, config)
        return

    if not offers:
        logger.info("No new matching free courses to notify")
        return

    notifier = TelegramNotifier(
        config.bot_token,
        config.chat_id,
        timeout=config.request_timeout,
    )

    hit_offers = [offer for offer in offers if is_hit(offer, config)]
    immediate = hit_offers[: config.max_hit_messages]
    immediate_ids = {offer.course_id for offer in immediate}
    digest_offers = [
        offer for offer in offers if offer.course_id not in immediate_ids
    ]

    sent = 0
    for offer in immediate:
        if notifier.send(_format_hit(offer)):
            state.mark(offer)
            state.save()
            sent += 1

    for message, chunk_offers in _digest_chunks(digest_offers):
        if notifier.send(message):
            for offer in chunk_offers:
                state.mark(offer)
                sent += 1
            state.save()

    logger.info("Telegram notifications delivered for %s course(s)", sent)
