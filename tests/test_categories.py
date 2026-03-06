# -*- coding: utf-8 -*-
"""Тесты для modules.categories: assign_category."""

import pytest
from categories import assign_category


class TestAssignCategory:
    """Тесты assign_category."""

    def test_empty(self):
        assert assign_category("") == ("📝", "Другое")
        assert assign_category("   ") == ("📝", "Другое")

    def test_home(self):
        emoji, name = assign_category("Купить продукты в магазине")
        assert emoji == "🏠"
        assert name == "Быт / дом"

        emoji, name = assign_category("Помыть посуду и убрать на кухне")
        assert emoji == "🏠"

        emoji, name = assign_category("Стирка и глажка")
        assert emoji == "🏠"

    def test_family(self):
        emoji, name = assign_category("Записать дочку к врачу")
        assert emoji == "👨‍👩‍👧"
        assert name == "Семья"

        emoji, name = assign_category("Собрать ребенка в школу")
        assert emoji == "👨‍👩‍👧"

    def test_care(self):
        emoji, name = assign_category("Записаться на маникюр")
        assert emoji == "💇‍♀️"
        assert name == "Уход / внешность"

        emoji, name = assign_category("Массаж в пятницу")
        assert emoji == "💇‍♀️"

    def test_self(self):
        emoji, name = assign_category("Погулять в парке")
        assert emoji == "🌿"
        assert name == "Для себя"

        emoji, name = assign_category("Почитать книгу")
        assert emoji == "🌿"

    def test_leisure(self):
        emoji, name = assign_category("Купить билеты в кино")
        assert emoji == "🎫"
        assert name == "Досуг"

    def test_errands(self):
        emoji, name = assign_category("Оплатить счета")
        assert emoji == "📦"
        assert name == "Дела / поручения"

        emoji, name = assign_category("Зарегистрировать домен")
        assert emoji == "📦"

    def test_projects(self):
        emoji, name = assign_category("Ремонт квартиры")
        assert emoji == "🏠"  # квартир + ремонт → home scores higher

        emoji, name = assign_category("Переезд в новый дом")
        assert emoji == "🧠"

    def test_other_fallback(self):
        emoji, name = assign_category("Позвонить коллеге")
        # «звонок» есть в errands
        assert emoji == "📦"

        emoji, name = assign_category("Xyz абвгд")
        assert emoji == "📝"
        assert name == "Другое"
