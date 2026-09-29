"""Org chart and org list: a lead whose reports come from several departments gets a branch per department."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


def person(uid, name, dept, manager=None, level=0):
    return {"id": uid, "name": name, "department": dept, "section": "", "designation": "",
            "level": level, "manager_id": manager, "status": "offline"}


# Rahul leads Compositing and Lighting people; Akash (Compositing) has two Compositing reports.
USERS = [person(1, "Rahul Verma", "Lighting", level=80),
         person(2, "Akash Singh", "Compositing", 1, level=60),
         person(3, "Priya Nair", "Compositing", 2), person(4, "Sana Qureshi", "Compositing", 2),
         person(5, "Arjun Rao", "Lighting", 1), person(6, "Meera Iyer", "Lighting", 1),
         person(7, "Dev Kapoor", "", 1)]


class OrgChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_one_branch_per_department_under_the_lead(self):
        from common.orgviews import OrgChart
        chart = OrgChart()
        chart.set_people(USERS)
        labels = [g["group"] for _, g in chart.group_boxes]
        # the lead's own department first, "No department" last
        self.assertEqual(labels, ["Lighting", "Compositing", "No department"])
        self.assertEqual(len(chart.boxes), len(USERS))             # everyone drawn exactly once
        self.assertEqual(chart.reports[1], 4)                      # the badge still counts people
        rahul = chart.boxes[1]
        for box, g in chart.group_boxes:
            self.assertGreater(box.top(), rahul.bottom())           # labels sit under the lead
            members = [u for u in USERS if u["manager_id"] == 1 and (u["department"] or "No department") == g["group"]]
            centers = [chart.boxes[u["id"]].center().x() for u in members]
            for u in members:                                        # each department's people below its label
                self.assertGreater(chart.boxes[u["id"]].top(), box.bottom())
            self.assertTrue(min(centers) - 1 <= box.center().x() <= max(centers) + 1)   # label centred over them
        # Akash's reports are all Compositing: no extra label under him
        self.assertEqual(sum(1 for _, g in chart.group_boxes if g["id"][1] == 2), 0)

    def test_single_department_team_is_unchanged(self):
        from common.orgviews import OrgChart
        chart = OrgChart()
        chart.set_people([u for u in USERS if u["department"] in ("Compositing",)])
        self.assertEqual(chart.group_boxes, [])

    def test_list_view_groups_by_department(self):
        from common import orgtree
        tree = orgtree.make_tree()
        orgtree.fill(tree, USERS, "reporting")
        rahul = tree.topLevelItem(0)
        self.assertTrue(rahul.text(0).startswith("Rahul Verma"))
        groups = [rahul.child(i).text(0) for i in range(rahul.childCount())]
        self.assertEqual(groups, ["Lighting · 2", "Compositing · 1", "No department · 1"])
        comp = rahul.child(1)
        self.assertTrue(comp.child(0).text(0).startswith("Akash Singh"))
        self.assertEqual(comp.child(0).childCount(), 2)           # Priya and Sana, no label

    def test_search_keeps_the_path_to_matches(self):
        from common import orgtree
        tree = orgtree.make_tree()
        orgtree.fill(tree, USERS, "reporting", query="priya")
        rahul = tree.topLevelItem(0)
        self.assertEqual(rahul.childCount(), 1)                   # one department left: no label needed
        self.assertTrue(rahul.child(0).text(0).startswith("Akash Singh"))
        self.assertTrue(rahul.child(0).child(0).text(0).startswith("Priya Nair"))


if __name__ == "__main__":
    unittest.main()
