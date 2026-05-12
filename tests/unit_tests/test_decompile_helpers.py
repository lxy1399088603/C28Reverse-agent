from react_agent.nodes.decompile import (
    _extract_candidate_callees_from_report,
    _merge_discovered_callees,
)


def test_extract_candidate_callees_from_report_reads_plain_names() -> None:
    report = """
Recovered C:
...

Candidate Callees:
- sub_8BB1B
- memcopy_8CF75
- _ti_sysbios_BIOS_start__E

Unresolved Evidence Gaps:
- none
"""

    assert _extract_candidate_callees_from_report(report) == [
        "sub_8BB1B",
        "memcopy_8CF75",
        "_ti_sysbios_BIOS_start__E",
    ]


def test_extract_candidate_callees_from_report_ignores_none_and_noise() -> None:
    report = """
Candidate Callees:
- none
- `*off_91F02` (indirect callee)
- sub_8AF0D
"""

    assert _extract_candidate_callees_from_report(report) == ["sub_8AF0D"]


def test_merge_discovered_callees_keeps_depth_first_order() -> None:
    updated = _merge_discovered_callees(
        "_main",
        ["_main"],
        ["InitGPIO_8B277", "sub_8BB1B"],
        ["sub_8BB1B", "memcopy_8CF75"],
        ["sub_8AF0D", "sub_8BB1B"],
    )

    assert updated == [
        "sub_8BB1B",
        "memcopy_8CF75",
        "sub_8AF0D",
        "InitGPIO_8B277",
    ]
