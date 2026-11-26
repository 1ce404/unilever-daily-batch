import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from task_variant_repair import native_defaults


class VariantReadbackTest(unittest.TestCase):
    def test_only_known_null_motion_defaults_are_equivalent(self):
        expected={'clips':[{'id':'s','props':{'volume':1}}]}
        actual={'clips':[{'id':'s','props':{'volume':1.0},'motion':None,'transitionIn':None,'transitionOut':None}]}
        self.assertEqual(native_defaults(expected),native_defaults(actual))
        actual['clips'][0]['motion']={'type':'zoom'}
        self.assertNotEqual(native_defaults(expected),native_defaults(actual))
        self.assertNotIn('motion',expected['clips'][0])


if __name__=='__main__':
    unittest.main()
