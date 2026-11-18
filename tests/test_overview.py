"""
[INPUT]: 依赖 scripts/overview.py 的 week_rows / day_count
[OUTPUT]: 总排期解析的单测
[POS]: tests 里总排期（⓪）的回归样例：表头日期过期、跨 5 天的整周格、跨两行的执行人、HC-MKT 子行
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from overview import day_count, week_rows  # noqa: E402

DOC = """## 1109-1112

<table><thead><tr><th>品牌</th><th>类型</th><th>1102</th><th>1103</th><th>1104</th><th>1105</th><th>1106</th><th>总计</th><th>执行</th></tr></thead><tbody>
<tr><td colspan="9">备注：命名标注好</td></tr>
<tr><td>clear</td><td>brief产出</td><td>160</td><td>180</td><td>170</td><td>155</td><td>140</td><td>805</td><td><cite user-name="陆春"></cite></td></tr>
<tr><td>PC-lux</td><td>brief产出</td><td colspan="5">30</td><td rowspan="2">90</td><td rowspan="2"><cite user-name="陈莹"></cite></td></tr>
<tr><td>PC-dove</td><td>brief产出</td><td colspan="5">60</td></tr>
<tr><td rowspan="2">HC-MKT</td><td>brief产出</td><td colspan="5">30</td><td></td><td><cite user-name="陈莹"></cite></td></tr>
<tr><td>内衣洗</td><td colspan="5">20</td><td>20</td><td><cite user-name="陈莹"></cite></td></tr>
<tr><td colspan="5"></td><td></td><td></td><td>1246</td><td></td></tr>
</tbody></table>

## 1116-1120
"""


class WeekRows(unittest.TestCase):
    def setUp(self):
        self.start, self.end, rows = week_rows(DOC, date(2026, 11, 10))
        self.rows = {r["line"]: r for r in rows}

    def test_week_found_by_heading_not_header_dates(self):
        self.assertEqual((self.start, self.end), (date(2026, 11, 9), date(2026, 11, 12)))
        self.assertEqual(day_count(self.rows["clear"], date(2026, 11, 10)), 180)  # 第 2 列 = 周二

    def test_colspan_is_weekly_total(self):
        self.assertEqual(self.rows["PC-lux"]["weekly"], "30")
        self.assertIsNone(day_count(self.rows["PC-lux"], date(2026, 11, 10)))

    def test_owner_spanning_two_rows(self):
        self.assertEqual(self.rows["PC-dove"]["owner"], "陈莹")

    def test_sub_row_under_merged_line(self):
        self.assertEqual(self.rows["HC-MKT · 内衣洗"]["weekly"], "20")
        self.assertNotIn("", self.rows)  # 合计行跳过


if __name__ == "__main__":
    unittest.main()
