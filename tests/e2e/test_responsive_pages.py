"""T143: responsive acceptance for all eight primary pages."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e]

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
PAGES = [
    ("overview", "/"),
    ("topology", "/pages/topology"),
    ("workloads", "/pages/workloads"),
    ("tasks", "/pages/tasks"),
    ("incidents", "/pages/incidents"),
    ("diagnosis", "/pages/diagnosis"),
    ("status", "/pages/status"),
    ("lab", "/pages/lab"),
]


def _layout_snapshot(page) -> dict:
    return page.evaluate(
        """() => {
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
              rect.width > 0 && rect.height > 0;
          };
          const rect = (el) => {
            const r = el.getBoundingClientRect();
            return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                    width: r.width, height: r.height};
          };
          const intersects = (a, b) =>
            Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
          const overlapSelectors = [
            ['.mainnav a', '.mainnav a'],
            ['main > .panel', 'main > .panel'],
            ['header.topbar', 'main.content'],
            ['main.content', 'footer.footer'],
          ];
          const overlaps = [];
          for (const [leftSelector, rightSelector] of overlapSelectors) {
            const left = [...document.querySelectorAll(leftSelector)].filter(visible);
            const right = [...document.querySelectorAll(rightSelector)].filter(visible);
            left.forEach((a, ai) => right.forEach((b, bi) => {
              if (a === b || (leftSelector === rightSelector && bi <= ai)) return;
              if (intersects(rect(a), rect(b))) {
                overlaps.push(`${leftSelector}[${ai}] overlaps ${rightSelector}[${bi}]`);
              }
            }));
          }
          const main = document.querySelector('main');
          return {
            overflow: document.documentElement.scrollWidth - window.innerWidth,
            overlaps,
            main: main ? rect(main) : null,
          };
        }"""
    )


@pytest.mark.parametrize(
    ("page_fixture", "viewport_name"),
    [("desktop_page", "desktop"), ("mobile_page", "mobile")],
)
def test_all_pages_are_responsive(
    request, base_url: str, page_fixture: str, viewport_name: str
) -> None:
    page = request.getfixturevalue(page_fixture)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    for name, path in PAGES:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None and response.ok, f"{path} failed to load"
        page.wait_for_selector("main.content", state="visible")
        page.wait_for_timeout(800)
        first = _layout_snapshot(page)
        page.wait_for_timeout(300)
        second = _layout_snapshot(page)

        assert first["overflow"] <= 1, (
            f"{viewport_name} {path} has {first['overflow']}px page overflow"
        )
        assert not first["overlaps"], (
            f"{viewport_name} {path} overlaps: {first['overlaps']}"
        )
        assert first["main"] is not None and second["main"] is not None
        assert abs(first["main"]["height"] - second["main"]["height"]) <= 1, (
            f"{viewport_name} {path} layout shifted after data load: "
            f"{first['main']['height']} -> {second['main']['height']}"
        )

        page.screenshot(
            path=str(SCREENSHOT_DIR / f"iter-10-{name}-{viewport_name}.png"),
            full_page=True,
        )
