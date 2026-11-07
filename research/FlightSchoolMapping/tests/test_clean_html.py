"""09a's clean_html must never delete <html> or <body>, whatever their class or id."""

import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("crawl09a", SCRIPTS / "09a_fleet_crawl.py")
crawl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crawl)


def test_body_with_boilerplate_class_survives():
    page = ('<html class="has-modal"><body class="page one-sidebar" id="widget-root">'
            '<main><p>Cessna 172S N12345 $165/hr wet</p></main>'
            '<div class="sidebar">Subscribe to our newsletter</div></body></html>')
    out = crawl.clean_html(page)
    assert "N12345" in out and "$165/hr" in out
    assert "newsletter" not in out  # boilerplate inside the body still goes


def test_boilerplate_wrapper_still_removed():
    out = crawl.clean_html('<body><div id="cookie-consent">We use cookies</div><p>Fleet</p></body>')
    assert "cookies" not in out and "Fleet" in out
