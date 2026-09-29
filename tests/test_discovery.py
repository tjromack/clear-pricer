from clear_pricer.discovery import find_location, normalise_url, parse_cms_hpt

RUSH_STYLE = """location-name: Rush University Medical Center\r
source-page-url: www.rush.edu/patients-visitors/billing/cost-care\r
mrf-url: apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx?dbName=dbRUMCCHICAGOIL&type=CDMWithoutLabel&fileType=CSV\r
contact-name: A Person\r
\r
location-name: Rush Oak Park Hospital, Inc\r
source-page-url: https://www.rush.edu/x\r
mrf-url: https://example.org/oak.csv\r
"""


def test_parses_blocks_and_fixes_missing_scheme():
    entries = parse_cms_hpt(RUSH_STYLE)
    assert len(entries) == 2
    rush = find_location(entries, "rush university medical center")
    assert rush.mrf_url.startswith("https://apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx?dbName=")
    assert rush.fixes == ("mrf_url_missing_scheme", "source_page_url_missing_scheme")
    assert find_location(entries, "Rush Oak Park Hospital, Inc").fixes == ()


def test_url_with_colon_in_value_is_kept_whole():
    [e] = parse_cms_hpt("location-name: X\nmrf-url: https://a.b/c:d.json\n")
    assert e.mrf_url == "https://a.b/c:d.json"


def test_missing_location_returns_none():
    assert find_location(parse_cms_hpt(RUSH_STYLE), "Nowhere General") is None


def test_normalise_url():
    assert normalise_url("https://x.org/a") == ("https://x.org/a", False)
    assert normalise_url("x.org/a") == ("https://x.org/a", True)
