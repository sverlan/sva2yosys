"""Contract of support.monitor_text."""

from support.monitor_text import without_wire_comments


def test_only_the_trailing_comment_of_condition_wires_is_removed() -> None:
    text = (
        "    assign e3 = !e2;  // (!previous_level)\n"
        "    assign e4 = e1 && e3;  // (level && (!previous_level))\n"
        "    assign c1 = (|e4);  // kept: not a condition wire\n"
        "    // a comment line stays\n"
        "    assign dis = c0;\n"
    )
    assert without_wire_comments(text) == (
        "    assign e3 = !e2;\n"
        "    assign e4 = e1 && e3;\n"
        "    assign c1 = (|e4);  // kept: not a condition wire\n"
        "    // a comment line stays\n"
        "    assign dis = c0;\n"
    )


def test_text_without_such_comments_is_unchanged() -> None:
    text = "module m;\n    assign e0 = i0;\nendmodule\n"
    assert without_wire_comments(text) == text
