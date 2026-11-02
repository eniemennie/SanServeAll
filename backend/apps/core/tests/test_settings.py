"""
Tests for shared settings that are easy to silently regress (someone
"cleans up" config/settings/base.py and the policy quietly reverts to
Django's own default with no test ever noticing).

Row: backend gap assessment, Auth 1.7 -- session idle timeout. Before this,
every session used Django's 2-week default with no idle expiry at all.
"""

from django.conf import settings


class TestSessionIdleTimeout:
    def test_session_cookie_age_is_a_short_few_hours_not_the_two_week_default(self):
        # Django's own default is 1209600 seconds (2 weeks). Anything
        # noticeably below "one day" proves this project set its own
        # explicit, shorter policy rather than inheriting the default.
        assert settings.SESSION_COOKIE_AGE < 60 * 60 * 24
        assert settings.SESSION_COOKIE_AGE > 0

    def test_session_save_every_request_is_enabled_so_the_timeout_is_idle_based(self):
        # Without this, SESSION_COOKIE_AGE counts from login time, not
        # from last activity -- an actively-working cashier or admin
        # would still get logged out mid-shift.
        assert settings.SESSION_SAVE_EVERY_REQUEST is True

    def test_session_expires_at_browser_close(self):
        assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is True
