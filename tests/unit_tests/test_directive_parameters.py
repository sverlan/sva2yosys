"""Contract of the shared parameter rule of ``sva2yosys.ir.directive``."""

from __future__ import annotations

import pytest

from sva2yosys.ir.directive import ParameterValue, uncomparable_message, uncomparable_parameters

_PREFIX = "is visible at this assertion but cannot be compared"
_SUFFIX = (
    " (a string, real or type parameter, an interface port, an escaped name); the monitor of "
    "each elaboration is selected by comparing integral parameters, so this is not supported"
)


def _int(literal: str = "3") -> ParameterValue:
    return ParameterValue(literal)


def test_no_parameters_have_no_uncomparable_one() -> None:
    assert uncomparable_parameters(()) == ()


def test_integer_parameters_are_comparable() -> None:
    parameters = (("K", ParameterValue("2", None)), ("N", _int("32'sd3")))
    assert uncomparable_parameters(parameters) == ()


def test_a_none_value_is_uncomparable() -> None:
    assert uncomparable_parameters((("N", _int()), ("upper", None))) == ("upper",)


def test_names_are_sorted() -> None:
    parameters = (("Z", None), ("A", _int()), ("M", None), ("B", None))
    assert uncomparable_parameters(parameters) == ("B", "M", "Z")


def test_an_escaped_name_is_uncomparable_even_with_an_integer_value() -> None:
    assert uncomparable_parameters((("P-x", _int()),)) == ("P-x",)


@pytest.mark.parametrize("name", ["1abc", "a b", "", "a.b", "a-b", "$x"])
def test_names_that_are_not_plain_identifiers_are_uncomparable(name: str) -> None:
    assert uncomparable_parameters(((name, _int()),)) == (name,)


@pytest.mark.parametrize("name", ["a", "_a", "A_1", "a$b", "N$", "_$"])
def test_plain_identifiers_are_comparable(name: str) -> None:
    assert uncomparable_parameters(((name, _int()),)) == ()


def test_a_bad_name_with_a_none_value_is_listed_once() -> None:
    assert uncomparable_parameters((("a-b", None),)) == ("a-b",)


def test_message_for_one_name() -> None:
    assert uncomparable_message(("upper",)) == f"upper {_PREFIX}{_SUFFIX}"


def test_message_for_several_names_is_comma_separated_in_the_given_order() -> None:
    assert uncomparable_message(("R", "S")) == f"R, S {_PREFIX}{_SUFFIX}"


def test_message_equals_the_text_of_the_pre_existing_rewrite_refusal() -> None:
    """The literal of the refusal that ``rewrite.py`` wrote before the conversion rule."""
    legacy = (
        "MODE is visible at this assertion but cannot be compared "
        "(a string, real or type parameter, an interface port, an escaped name); the "
        "monitor of each elaboration is selected by comparing integral parameters, so "
        "this is not supported"
    )
    assert uncomparable_message(("MODE",)) == legacy
