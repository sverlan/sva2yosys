"""Unit contract of the ``inside`` oracle (``support.inside_reference``, IEEE 1800-2017
11.4.13). Every expected value is derived by hand from the clause."""

from __future__ import annotations

import pytest

from support.inside_reference import FillItem, Item, Operand, RangeItem, ValueItem, inside


def u(value: int, width: int) -> Operand:
    return Operand(value, width, False)


def s(value: int, width: int) -> Operand:
    """A signed operand given by its bit pattern."""
    return Operand(value, width, True)


def test_empty_set_matches_nothing() -> None:
    assert not inside(u(3, 4), [])


def test_value_items_match_by_equality() -> None:
    items: list[Item] = [ValueItem(u(1, 4)), ValueItem(u(6, 4))]
    assert inside(u(6, 4), items)
    assert not inside(u(5, 4), items)


def test_wildcard_bits_of_the_item_are_dont_care() -> None:
    # 4'b1?0? matches 1000, 1001, 1100, 1101
    item = [ValueItem(u(0b1000, 4), wildcard=0b0101)]
    assert [v for v in range(16) if inside(u(v, 4), item)] == [8, 9, 12, 13]


def test_all_wildcard_item_matches_everything() -> None:
    assert all(inside(u(v, 3), [ValueItem(u(0, 3), wildcard=0b111)]) for v in range(8))


def test_range_is_inclusive_on_both_ends() -> None:
    items = [RangeItem(u(2, 4), u(5, 4))]
    assert [v for v in range(16) if inside(u(v, 4), items)] == [2, 3, 4, 5]


def test_reversed_range_matches_nothing() -> None:
    # lo <= e && e <= hi is unsatisfiable for lo > hi
    assert not any(inside(u(v, 4), [RangeItem(u(5, 4), u(2, 4))]) for v in range(16))


def test_single_point_range() -> None:
    assert [v for v in range(8) if inside(u(v, 3), [RangeItem(u(4, 3), u(4, 3))])] == [4]


def test_signed_range_with_negative_bounds() -> None:
    # 3-bit signed e in [-2:1]: -2 (110), -1 (111), 0, 1
    items = [RangeItem(s(0b110, 3), s(0b001, 3))]
    assert [v for v in range(8) if inside(s(v, 3), items)] == [0, 1, 6, 7]


def test_signed_operand_against_unsigned_item_compares_unsigned() -> None:
    # 3-bit signed e = -1 (111) against the unsigned 4-bit item 15: both unsigned, 7 != 15
    assert not inside(s(0b111, 3), [ValueItem(u(15, 4))])
    # the unsigned 4-bit item 7 matches 111 zero-extended to 0111
    assert inside(s(0b111, 3), [ValueItem(u(7, 4))])


def test_signed_operand_against_wider_signed_item_sign_extends() -> None:
    # 3-bit signed -1 equals the 4-bit signed -1 (1111)
    assert inside(s(0b111, 3), [ValueItem(s(0b1111, 4))])
    assert not inside(s(0b111, 3), [ValueItem(s(0b0111, 4))])


def test_unsigned_range_bound_makes_the_comparison_unsigned() -> None:
    # e = -1 (3-bit signed, pattern 111) against the unsigned range [0:7]: both comparisons are
    # unsigned, e is 7, and 0 <= 7 <= 7 holds. With signed bounds [0:3], e = -1 is below 0.
    assert inside(s(0b111, 3), [RangeItem(u(0, 3), u(7, 3))])
    assert not inside(s(0b111, 3), [RangeItem(s(0, 3), s(3, 3))])


def test_each_comparison_is_sized_on_its_own() -> None:
    # e = 4'b1111 unsigned. The 2-bit item 3 is zero-extended (0011): no match; the 4-bit item
    # 15 matches. The first item's width does not change the second comparison.
    assert not inside(u(15, 4), [ValueItem(u(3, 2))])
    assert inside(u(15, 4), [ValueItem(u(3, 2)), ValueItem(u(15, 4))])


def test_wildcard_msb_of_a_signed_item_extends_as_wildcard() -> None:
    # e = 4-bit signed 0b0101; the 2-bit signed item 2'sb?1 has a wildcard msb: the extension
    # bits are wildcards too, so only bit 0 (= 1) is compared: 0101 has bit0 = 1: match
    item = [ValueItem(s(0b01, 2), wildcard=0b10)]
    assert inside(s(0b0101, 4), item)
    assert not inside(s(0b0100, 4), item)


def test_known_msb_of_a_signed_item_extends_by_sign() -> None:
    # 2'sb1? (msb 1): extends with ones, so 4-bit e must be 11?? in the upper bits: 1110/1111
    item = [ValueItem(s(0b10, 2), wildcard=0b01)]
    assert [v for v in range(16) if inside(s(v, 4), item)] == [0b1110, 0b1111]


def test_operand_value_must_fit_its_width() -> None:
    with pytest.raises(ValueError):
        Operand(16, 4, False)


def test_dollar_bounds_are_the_extremes_of_the_left_type_unsigned() -> None:
    # 3-bit unsigned: lowest 0, highest 7
    low = [v for v in range(8) if inside(u(v, 3), [RangeItem(None, u(2, 3))])]
    high = [v for v in range(8) if inside(u(v, 3), [RangeItem(u(5, 3), None)])]
    assert (low, high) == ([0, 1, 2], [5, 6, 7])


def test_dollar_bounds_are_the_extremes_of_the_left_type_signed() -> None:
    # 3-bit signed: lowest -4 (100), highest 3 (011). [$:-2] = -4..-2 (patterns 4, 5, 6),
    # [2:$] = 2, 3
    items_low = [RangeItem(None, s(0b110, 3))]
    items_high = [RangeItem(s(2, 3), None)]
    assert [v for v in range(8) if inside(s(v, 3), items_low)] == [4, 5, 6]
    assert [v for v in range(8) if inside(s(v, 3), items_high)] == [2, 3]


def test_dollar_on_both_sides_matches_every_value() -> None:
    assert all(inside(s(v, 3), [RangeItem(None, None)]) for v in range(8))
    assert all(inside(u(v, 3), [RangeItem(None, None)]) for v in range(8))


def test_dollar_with_a_bound_beyond_the_type_range() -> None:
    # the unsigned 3-bit left against the 4-bit bound 12: [$:12] holds for all, [12:$] for none
    assert all(inside(u(v, 3), [RangeItem(None, u(12, 4))]) for v in range(8))
    assert not any(inside(u(v, 3), [RangeItem(u(12, 4), None)]) for v in range(8))


def test_fill_literals_fill_the_width_of_the_operand() -> None:
    # 5.7.1: '1 against a 4-bit operand is 1111, '0 is 0000; 'x and 'z are all wildcards
    assert [v for v in range(16) if inside(u(v, 4), [FillItem("1")])] == [15]
    assert [v for v in range(16) if inside(u(v, 4), [FillItem("0")])] == [0]
    assert all(inside(u(v, 4), [FillItem("x")]) for v in range(16))
    assert all(inside(s(v, 3), [FillItem("z")]) for v in range(8))


def test_fill_literal_is_unsigned_so_a_signed_operand_is_compared_by_pattern() -> None:
    # -1 as a 3-bit signed operand is pattern 111, which '1 matches
    assert inside(s(0b111, 3), [FillItem("1")])
    assert not inside(s(0b011, 3), [FillItem("1")])
