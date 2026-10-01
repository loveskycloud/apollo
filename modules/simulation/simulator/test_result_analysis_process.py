import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from task_service import TaskService


class AnalysisProcessTests(unittest.TestCase):
    def test_cancel_analysis_terminates_only_its_child_and_errors_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Exercises the real subprocess/cancellation boundary without
            # requiring a lengthy Apollo record for this lifecycle test.
            (root/'result_analysis.py').write_text('''import json,os,sys,time
from pathlib import Path
r=json.loads(Path(sys.argv[2]).read_text());p=Path(r['job_dir'])
(p/'ready').write_text(str(os.getpid()))
if r.get('error'): raise RuntimeError('Invalid record fixture')
time.sleep(r.get('delay',0))
(p/'analysis.json').write_text(json.dumps({'child_pid':os.getpid()}))
''')
            service = TaskService(root/'queue')
            try:
                for name in ('cancel', 'success', 'error'):
                    (root/name).mkdir()
                    service.jobs[name]={'id':name,'stage':'result_analysis','history':[]}
                with patch('task_service._SIM_DIR', root), concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    cancelled = pool.submit(service.analyze_results, 'cancel', {'job_dir':str(root/'cancel'),'delay':30})
                    deadline=time.monotonic()+5
                    while not (root/'cancel/ready').exists() and time.monotonic()<deadline:
                        time.sleep(.01)
                    self.assertTrue((root/'cancel/ready').exists())
                    success = pool.submit(service.analyze_results, 'success', {'job_dir':str(root/'success')})
                    service.request({'action':'cancel','id':'cancel'})
                    with self.assertRaises(InterruptedError):
                        cancelled.result(timeout=5)
                    self.assertGreater(success.result(timeout=5)['child_pid'],0)
                    self.assertIsNotNone(service.children['cancel'].poll())
                    with self.assertRaisesRegex(RuntimeError, 'Invalid record fixture'):
                        service.analyze_results('error',{'job_dir':str(root/'error'),'error':True})
            finally:
                service.close()

    def test_stage_updates_are_live_but_terminal_state_is_persisted_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService(directory)
            try:
                service.jobs['job']={'stage':'queued','history':[]}
                service.last_progress_save=100
                with patch('task_service.time.monotonic',return_value=100.1), patch.object(service,'save') as save:
                    service.update('job',stage='profile_update')
                    self.assertEqual(service.jobs['job']['stage'],'profile_update')
                    save.assert_not_called()
                    service.update('job',stage='completed',progress=100)
                    save.assert_called_once()
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
