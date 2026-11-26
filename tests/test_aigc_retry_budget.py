import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aigc_gen import Run


class StrictGenerationBudgetTest(unittest.TestCase):
    def run_object(self, directory):
        p=Path(directory)/'plan.json'
        p.write_text(json.dumps({'product':'p','tag':'AI-1126-test','max_retries':0,'scenes':[{'id':'s','prompts':['original creative']}]}))
        return Run({'products':{'p':{'clipo_product_id':'owned-product'}}},p)

    def test_failed_first_attempt_cannot_spend_again(self):
        with tempfile.TemporaryDirectory() as d:
            r=self.run_object(d);slot=r.st['slots']['s-01'];slot['attempts']=[{'status':'failed','prompt':'original creative'}]
            with patch('aigc_gen.cb.api') as api:
                self.assertEqual(r.submit_free(),[]);api.assert_not_called()
            self.assertIn('gave_up',Run(r.cfg,r.plan_path).st['slots']['s-01'])

    def test_unknown_result_stops_second_submission(self):
        with tempfile.TemporaryDirectory() as d:
            r=self.run_object(d)
            with patch('aigc_gen.cb.api',side_effect=RuntimeError('network interrupted')) as api:
                with self.assertRaises(RuntimeError):r.submit_free()
                with self.assertRaisesRegex(RuntimeError,'Uncertain'):r.submit_free()
                self.assertEqual(api.call_count,1)


if __name__=='__main__':
    unittest.main()
