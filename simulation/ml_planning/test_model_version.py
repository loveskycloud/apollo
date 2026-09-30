import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path[:0]=['/opt/apollo/neo/python',str(Path(__file__).resolve().parent)]
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION']='python'
from model_selection import read_model_selection
from google.protobuf.text_format import ParseError


class ModelVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conf=self.root/'conf/ml_planning.pb.txt'
        self.conf.parent.mkdir()
        for version in ('v3','v4'):
            target=self.root/'models'/version/'unified.weights'
            target.parent.mkdir(parents=True)
            target.write_text(version+' weights')

    def test_config_alone_selects_version_even_with_stale_environment(self):
        previous=os.environ.get('ML_PLANNING_WEIGHTS')
        os.environ['ML_PLANNING_WEIGHTS']='/missing/old/environment.weights'
        try:
            for version in ('v3','v4'):
                self.conf.write_text(f'model_version: "{version}"\n')
                selected=read_model_selection(self.conf,self.root/'models')
                self.assertEqual(selected['version'],version)
                self.assertEqual(Path(selected['weights']).parent.name,version)
                self.assertEqual(selected['sha256'],hashlib.sha256((version+' weights').encode()).hexdigest())
        finally:
            if previous is None:os.environ.pop('ML_PLANNING_WEIGHTS',None)
            else:os.environ['ML_PLANNING_WEIGHTS']=previous

    def test_invalid_missing_and_unknown_versions_do_not_fall_back(self):
        for text in ('','model_version: ""','model_version: "../v4"','model_version: "latest"','unknown: "v4"','model_version: "v5"'):
            with self.subTest(config=text):
                self.conf.write_text(text)
                with self.assertRaises((ValueError, FileNotFoundError, ParseError)):
                    read_model_selection(self.conf,self.root/'models')

    def test_frozen_selector_and_weights_survive_source_switch(self):
        self.conf.write_text('model_version: "v4"\n')
        frozen=self.root/'job/modules/simulation/ml_planning'
        (frozen/'conf').mkdir(parents=True)
        shutil.copy2(self.conf,frozen/'conf/ml_planning.pb.txt')
        selected=read_model_selection(frozen/'conf/ml_planning.pb.txt',self.root/'models')
        destination=frozen/'models'/selected['version']/'unified.weights'
        destination.parent.mkdir(parents=True)
        shutil.copy2(selected['weights'],destination)
        self.conf.write_text('model_version: "v3"\n')
        Path(selected['weights']).write_text('changed source')
        actual=read_model_selection(frozen/'conf/ml_planning.pb.txt',frozen/'models')
        self.assertEqual(actual['version'],'v4')
        self.assertEqual(actual['sha256'],selected['sha256'])


if __name__=='__main__':unittest.main()
