"""Tests for Telegram notification mode."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest import mock

from udemy_enroller.notifications import (
    CourseOffer,
    NotificationConfig,
    NotificationState,
    UdemyOfferInspector,
    is_hit,
    notify_free_courses,
    offer_matches,
)


def _offer(**overrides):
    values = {
        "course_id": 123,
        "title": "ChatGPT Automation with Python",
        "coupon_code": "FREE100",
        "url": "https://www.udemy.com/course/example/?couponCode=FREE100",
        "language": "English",
        "category": "Development",
        "subcategory": "Python",
        "rating": 4.7,
        "students": 2500,
        "duration": "5 hours",
        "instructor": "Example Teacher",
        "regular_price": 99.99,
    }
    values.update(overrides)
    return CourseOffer(**values)


def _config(**overrides):
    values = {
        "bot_token": "token",
        "chat_id": "123",
        "state_file": "unused.json",
    }
    values.update(overrides)
    return NotificationConfig(**values)


def test_parse_coupon_link_keeps_other_query_parameters():
    clean_url, coupon = UdemyOfferInspector._parse_coupon_link(
        "https://www.udemy.com/course/example/?couponCode=FREE100&utm_source=test"
    )

    assert coupon == "FREE100"
    assert "couponCode" not in clean_url
    assert "utm_source=test" in clean_url


def test_inspector_uses_udemy_headers_and_resolves_course_id():
    session = mock.Mock()
    response = mock.Mock(
        content=b'<html><body data-clp-course-id="6737247"></body></html>'
    )
    session.get.return_value = response

    with mock.patch(
        "udemy_enroller.notifications.requests.Session", return_value=session
    ):
        inspector = UdemyOfferInspector()

    assert inspector._get_course_id("https://www.udemy.com/course/example/") == 6737247
    session.headers.update.assert_called_once_with(inspector.UDEMY_HEADERS)
    session.get.assert_called_once_with(
        "https://www.udemy.com/course/example/", timeout=20
    )
    response.raise_for_status.assert_called_once_with()


def test_missing_course_id_is_skipped_without_failing_notification_run(tmp_path):
    class Scrapers:
        async def run(self):
            return ["https://www.udemy.com/course/example/?couponCode=FREE100"]

    session = mock.Mock()
    session.get.return_value = mock.Mock(content=b"<html><body></body></html>")
    config = _config(state_file=str(tmp_path / "state.json"))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        with (
            mock.patch(
                "udemy_enroller.notifications.NotificationConfig.from_env",
                return_value=config,
            ),
            mock.patch(
                "udemy_enroller.notifications.ScraperManager",
                return_value=Scrapers(),
            ),
            mock.patch(
                "udemy_enroller.notifications.requests.Session", return_value=session
            ),
        ):
            notify_free_courses(True, False, False, False, False, 1, dry_run=True)
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    session.get.return_value.raise_for_status.assert_called_once_with()


def test_offer_filters_are_case_insensitive():
    config = _config(
        languages=("english",),
        categories=("python",),
        include_keywords=("chatgpt",),
        exclude_keywords=("forex",),
        min_rating=4.5,
        min_students=1000,
    )

    assert offer_matches(_offer(), config)


def test_offer_filter_rejects_excluded_keyword():
    config = _config(exclude_keywords=("forex",))

    assert not offer_matches(_offer(title="Forex Automation Course"), config)


def test_hit_uses_rating_and_student_thresholds():
    config = _config(hit_min_rating=4.5, hit_min_students=1000)

    assert is_hit(_offer(), config)
    assert not is_hit(_offer(rating=4.4), config)
    assert not is_hit(_offer(students=999), config)


def test_notification_state_suppresses_recent_course(tmp_path):
    state_file = tmp_path / "notified_courses.json"
    state = NotificationState(str(state_file), memory_days=60)
    offer = _offer()

    assert state.should_notify(offer.course_id)
    state.mark(offer)
    state.save()

    reloaded = NotificationState(str(state_file), memory_days=60)
    assert not reloaded.should_notify(offer.course_id)


def test_notification_state_purges_old_course(tmp_path):
    state_file = tmp_path / "notified_courses.json"
    old = datetime.now(timezone.utc) - timedelta(days=61)
    state_file.write_text(
        json.dumps(
            {
                "courses": {
                    "123": {
                        "notified_at": old.isoformat(),
                        "coupon_code": "OLD",
                        "title": "Old course",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    state = NotificationState(str(state_file), memory_days=60)

    assert state.should_notify(123)
