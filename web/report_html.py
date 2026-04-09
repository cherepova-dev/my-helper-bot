# -*- coding: utf-8 -*-
"""Текст отчётов бота (*жирный*, _курсив_) → безопасный HTML для веба."""
from __future__ import annotations

import html


def report_text_to_html(text: str) -> str:
    if not text or not text.strip():
        return '<p class="report-line report-line--muted">Нет данных.</p>'
    out: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            out.append('<div class="report-gap" aria-hidden="true"></div>')
            continue
        if line.strip().startswith("· · ·"):
            out.append('<hr class="report-rule" />')
            continue
        css = "report-line"
        if line.startswith("  ▸"):
            css += " report-line--item"
        out.append(f'<p class="{css}">{_inline_to_html(line)}</p>')
    return "\n".join(out)


def _inline_to_html(s: str) -> str:
    """Обрабатывает *bold* и _italic_ без вложенности."""
    s = str(s)
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "*":
            j = s.find("*", i + 1)
            if j > i:
                inner = html.escape(s[i + 1 : j])
                out.append(f"<strong>{inner}</strong>")
                i = j + 1
                continue
        if s[i] == "_":
            j = s.find("_", i + 1)
            if j > i:
                inner = html.escape(s[i + 1 : j])
                out.append(f"<em>{inner}</em>")
                i = j + 1
                continue
        out.append(html.escape(s[i]))
        i += 1
    return "".join(out)
