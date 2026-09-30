"""End-to-end tests against real Chromium. Skipped if no browser is available."""

from pathlib import Path

import pytest

from humanized_input import BUILTIN_PROFILES, audit
from humanized_input.session import user_session

PAGE = (Path(__file__).parent.parent / "examples" / "signup.html").resolve().as_uri()


@pytest.fixture(params=list(BUILTIN_PROFILES))
def profile(request):
    return BUILTIN_PROFILES[request.param].with_seed(7)


@pytest.fixture(scope="module", autouse=True)
def _require_browser():
    try:
        with user_session(PAGE, "expert"):
            pass
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"browser unavailable: {exc}", allow_module_level=True)


def _session(profile):
    return user_session(PAGE, profile)


def test_every_profile_can_sign_up(profile):
    with _session(profile) as user:
        assert user.fill("Full name", "Grace Hopper")
        assert user.fill("Email", "grace@example.org")
        assert user.check("agree to the terms")
        assert user.press("Create account")
        spoken = [u.text for u in user.sr.transcript if u.kind == "speech"]
        assert "Welcome - Example Library" in spoken


def test_typed_text_lands_in_the_field_despite_typos():
    sloppy = BUILTIN_PROFILES["novice"].with_seed(3)
    with _session(sloppy) as user:
        user.fill("Email", "someone.long.address@example.org")
        assert user.backend.page.input_value("#email") == "someone.long.address@example.org"


def test_validation_errors_are_announced_like_a_live_region():
    with _session(BUILTIN_PROFILES["expert"].with_seed(1)) as user:
        user.fill("Email", "not-an-email")
        user.press("Create account")
        spoken = [u.text for u in user.sr.transcript if u.kind == "speech"]
        assert any("Enter a valid email address." in s for s in spoken)


def test_unlabelled_field_is_found_only_by_guessing():
    with _session(BUILTIN_PROFILES["expert"].with_seed(1)) as user:
        assert user.fill("Library card", "123")
        assert "no accessible name" in user.report.goals[-1].note


def test_audit_finds_the_planted_bugs():
    with _session(BUILTIN_PROFILES["expert"]) as user:
        rules = sorted(i.rule for i in audit(user.sr.title, user.sr.items))
    assert rules == ["heading-skip", "unnamed-control", "unnamed-control"]
