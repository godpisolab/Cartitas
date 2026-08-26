"""Tests de parse_price_text() / parse_price_minor_unit() -- sección 1.2
del plan de pruebas. Lógica pura, sin red ni BBDD."""

from __future__ import annotations

import pytest

from base_script import parse_price_minor_unit, parse_price_text


class TestParsePriceText:
    @pytest.mark.parametrize("text,expected", [
        ("12,95€", 12.95),
        ("1.234,56", 1234.56),  # formato español con miles
        ("1234.56", 1234.56),  # formato anglosajón
        ("€ 12,95", 12.95),  # símbolo antes, con espacio de por medio
        (12.5, 12.5),  # float directo, sin pasar por el regex
        (16, 16.0),  # int directo
        (None, None),
        ("", None),
        ("precio no disponible", None),  # sin dígitos -- no debe lanzar excepción
        ("   ", None),  # solo espacios
    ])
    def test_parse_price_text(self, text, expected):
        assert parse_price_text(text) == expected


class TestParsePriceMinorUnit:
    def test_minor_unit_2_decimales(self):
        assert parse_price_minor_unit("1600", minor_unit=2) == 16.00

    def test_minor_unit_por_defecto_es_2(self):
        assert parse_price_minor_unit("1600") == 16.00

    def test_minor_unit_0_decimales(self):
        assert parse_price_minor_unit("1600", minor_unit=0) == 1600.0

    def test_none_no_revienta(self):
        assert parse_price_minor_unit(None) is None

    def test_texto_no_numerico_no_lanza_valueerror(self):
        assert parse_price_minor_unit("abc") is None

    def test_acepta_int_directo(self):
        assert parse_price_minor_unit(1600) == 16.00
