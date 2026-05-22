"""HTML 提取器。"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .helpers import clean_text


class LinkAndTextParser(HTMLParser):
    """Extracts title, links and visible text from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[tuple[str, str]] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._ignored_tag_depth = 0
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    @staticmethod
    def _extract_url_from_attrs(attrs_dict: dict[str, str | None]) -> str | None:
        direct_attr_names = (
            "href",
            "data-href",
            "data-url",
            "data-link",
            "data-src",
            "data-jump",
        )
        for attr_name in direct_attr_names:
            value = attrs_dict.get(attr_name)
            if value and value.strip() and not value.strip().lower().startswith("javascript:"):
                return value.strip()

        script_attr_names = ("onclick", "onmousedown", "data-onclick")
        patterns = (
            re.compile(r"""window\.open\(\s*['"]([^'"]+)['"]""", re.IGNORECASE),
            re.compile(r"""location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE),
            re.compile(r"""open\(\s*['"]([^'"]+)['"]""", re.IGNORECASE),
        )
        for attr_name in script_attr_names:
            value = attrs_dict.get(attr_name)
            if not value:
                continue
            for pattern in patterns:
                match = pattern.search(value)
                if match:
                    return match.group(1).strip()
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._ignored_tag_depth += 1
        elif lowered == "title":
            self._in_title = True
        elif lowered == "a":
            self._current_href = self._extract_url_from_attrs(attrs_dict)
            self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._ignored_tag_depth:
            self._ignored_tag_depth -= 1
        elif lowered == "title":
            self._in_title = False
        elif lowered == "a":
            if self._current_href:
                link_text = clean_text(" ".join(self._current_link_text))
                self.links.append((self._current_href, link_text))
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_tag_depth:
            return
        if self._in_title:
            self.title += data
            return
        text = clean_text(data)
        if not text:
            return
        self.text_parts.append(text)
        if self._current_href is not None:
            self._current_link_text.append(text)
