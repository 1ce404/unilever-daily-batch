import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from review_rule import revise
class ReviewRuleTest(unittest.TestCase):
 def test_preserves_other_rules(self):self.assertEqual(revise('one\nold\nthree','old','new'),'one\nnew\nthree')
 def test_changed_or_ambiguous_prompt_stops(self):
  for text in ['missing','old old']:
   with self.assertRaises(RuntimeError):revise(text,'old','new')
