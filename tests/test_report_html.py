# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.report_html import report_text_to_html


def test_bold_and_italic():
    html = report_text_to_html("Привет *мир* и _курсив_")
    assert "<strong>мир</strong>" in html
    assert "<em>курсив</em>" in html
    assert "<script>" not in html.lower()


def test_escapes_html():
    html = report_text_to_html("<b>x</b>")
    assert "&lt;" in html
