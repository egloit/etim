from etim10_export.import_lobster_rules import Importer

PIM = {"type": "linked", "pim": ["PIM_FAS_09_1:data-4237"]}


def _compare(code, hit, other):
    return {"id": "105", "args": {"a": {"type": "constant", "value": code}, "b": PIM,
                                  "c": {"type": "constant", "value": str(hit)},
                                  "d": {"type": "constant", "value": str(other)}}}


def _break(value):
    return {"id": "223", "args": {"a": {"type": "constant", "value": value}}}


def test_switch_continues_after_case_even_if_goto_skips_a_compare():
    # Like EF009346 in the Lobster profile: the 2nd compare's goto d=6 skips
    # the compare at 5 and lands on its break - Lobster never reaches 4000.
    fns = [
        _compare("PIM_FAS_09_1_2700", 2, 3), _break("2700"),
        _compare("PIM_FAS_09_1_2790", 4, 6), _break("2700"),
        _compare("PIM_FAS_09_1_4000", 6, 7), _break("4000"),
        _break("-"),
    ]

    _, cases, default, _ = Importer._switch(fns)

    assert cases == {"PIM_FAS_09_1_2700": "2700", "PIM_FAS_09_1_2790": "2700", "PIM_FAS_09_1_4000": "4000"}
    assert default == "-"


def test_switch_correct_chain_unchanged():
    fns = [_compare("A", 2, 3), _break("1"), _compare("B", 4, 5), _break("2"), _break("0")]

    _, cases, default, _ = Importer._switch(fns)

    assert cases == {"A": "1", "B": "2"}
    assert default == "0"
