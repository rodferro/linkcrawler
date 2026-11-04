from __future__ import annotations

import pytest

from linkcrawler.urls import dedup_key, host_of, normalize_url, same_site

BASE = "https://example.com/docs/guide/"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        # relative links are resolved (the old crawler ignored them entirely)
        ("intro", "https://example.com/docs/guide/intro"),
        ("/about", "https://example.com/about"),
        ("../api", "https://example.com/docs/api"),
        ("./a/./b/../c", "https://example.com/docs/guide/a/c"),
        ("//cdn.example.org/x", "https://cdn.example.org/x"),
        ("?page=2", "https://example.com/docs/guide?page=2"),
        # fragments are dropped
        ("#section", "https://example.com/docs/guide"),
        ("/about#team", "https://example.com/about"),
        # trailing slashes are unified, root keeps its slash
        ("/about/", "https://example.com/about"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/", "https://example.com/"),
        # scheme/host case and default ports
        ("HTTPS://Example.COM/Path", "https://example.com/Path"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("http://example.com:8080/x", "http://example.com:8080/x"),
        # query strings are preserved verbatim
        ("/s?b=2&a=1", "https://example.com/s?b=2&a=1"),
        ("  /padded  ", "https://example.com/padded"),
    ],
)
def test_normalize_resolves_and_canonicalizes(href: str, expected: str) -> None:
    assert normalize_url(href, BASE) == expected


@pytest.mark.parametrize(
    "href",
    [
        "",
        "   ",
        "mailto:someone@example.com",
        "javascript:void(0)",
        "tel:+123",
        "ftp://example.com/file",
        "data:text/html,hi",
        "http://",
        "http://example.com:99999/",
    ],
)
def test_normalize_rejects_uncrawlable(href: str) -> None:
    assert normalize_url(href, BASE) is None


def test_equivalent_urls_normalize_identically() -> None:
    variants = [
        "https://example.com/about",
        "https://example.com/about/",
        "https://EXAMPLE.com/about#x",
        "https://example.com:443/about",
        "/about",
        "../../about/",
    ]
    assert {normalize_url(v, BASE) for v in variants} == {"https://example.com/about"}


def test_same_site_ignores_www() -> None:
    assert same_site("https://www.example.com/x", "example.com")
    assert same_site("https://example.com/x", "www.example.com")
    assert not same_site("https://blog.example.com/x", "example.com")
    assert host_of("http://example.com:8080/x") == "example.com:8080"


def test_dedup_key_ignores_scheme_only() -> None:
    assert dedup_key("http://example.com/a") == dedup_key("https://example.com/a")
    assert dedup_key("http://example.com/a") != dedup_key("http://example.com/b")
    assert dedup_key("http://example.com:8080/") != dedup_key("http://example.com/")
