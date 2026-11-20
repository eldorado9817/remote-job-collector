"""Tests for strictly current Pacific date, role, remote location, aggregation, migration."""
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import collect_remote_jobs as c
import combine_shard_results as combine
import merge_job_results as merge

class FilterTodayTests(unittest.TestCase):
    def setUp(self):
        self.old = c.SETTINGS.copy()
        c.SETTINGS.update(scope='usuk', roles='software-ai', workplace='remote',
                          allow_undated=False, all_dates=False, reference_date=date(2026,11,20))
    def tearDown(self):
        c.SETTINGS.clear(); c.SETTINGS.update(self.old)

    def job(self, title='Senior Software Engineer', location='Remote - United States', date_val='2026-11-20T17:00:00Z', **kwargs):
        return dict(title=title,location=location, date=date_val,id='1',remote=True,
                    url='https://example.com/1',date_source='releasedDate',**kwargs)
    def rows(self,**kwargs):
        return c.normalize_generic('SmartRecruiters','Acme','acme',self.job(**kwargs),1)

    def test_title_categories(self):
        for title in ('Senior Software Engineer', 'AI Engineer','Senior Backend Developer',
                      'Full-Stack Engineer','React Developer','Python Developer','Platform Engineer',
                      'Site Reliability Engineer','Machine Learning Engineer', 'MLOps Engineer',
                      'Data Scientist', 'Generative AI Researcher', 'AI Trainer',
                      'Research Scientist', 'Prompt Engineer', 'LLM Evaluation Specialist'):
            with self.subTest(title=title):
                self.assertEqual(len(self.rows(title=title)),1)
        for title in ('Marketing Manager','Account Executive','Accountant',
                      'Mechanical Engineer','Sales Engineer','Product Manager'):
            with self.subTest(title=title):
                self.assertEqual(self.rows(title=title),[])

    def test_pacific_midnight_boundaries(self):
        for val in ('2026-11-20T08:00:00Z','2026-11-21T07:59:59Z','2026-11-20'):
            with self.subTest(val=val):
                self.assertEqual(self.rows(date_val=val)[0]['published_date_pt'],'2026-11-20')
        for val in ('2026-11-20T07:59:59Z','2026-11-21T08:00:00Z',
                    '2026-11-19','2026-11-21','2026-11-20T17:00:00'):
            with self.subTest(val=val):
                self.assertEqual(self.rows(date_val=val),[])
        # 00:30 London, Nov 20 is 4:30 PM Pacific on Nov 19; not today's PT job.
        self.assertEqual(self.rows(date_val='2026-11-20T00:30:00+00:00'),[])

    def test_excludes_old_undated_hybrid_onsite_and_non_us_uk(self):
        self.assertEqual(self.rows(date_val=None),[])
        self.assertEqual(self.rows(date_val='2026-11-15T17:00:00Z'),[])
        self.assertEqual(self.rows(location='Hybrid - US'),[])
        self.assertEqual(self.rows(location='On-site London, UK'),[])
        outside = self.rows(location='Remote - Germany')
        self.assertEqual(outside[0]['country'],'Review: location unspecified')
        self.assertEqual(self.rows(location='Remote - UK')[0]['country'],'UK')
        self.assertEqual(self.rows(location='Remote - Worldwide')[0]['country'],'US & UK')

    def test_greenhouse_fetches_detail_date_not_updated_at(self):
        listed = {'jobs':[{'title':'Software Engineer','id':13,
                  'location':{'name':'Remote - United States'},
                  'updated_at':'2026-11-20T17:00:00Z'}]}
        detail = {'title':'Software Engineer','id':13,
                  'location':{'name':'Remote - United States'},
                  'first_published':'2026-11-19T17:00:00Z',
                  'absolute_url':'https://example.com/13'}
        def fake(url, payload=None):
            return detail if '/jobs/13' in url else listed
        with patch.object(c,'get_json',side_effect=fake) as mocked:
            self.assertEqual(c.jobs_greenhouse('Acme','acme',1),[])
            self.assertEqual(mocked.call_count,2)
        detail['first_published']='2026-11-20T17:00:00Z'
        with patch.object(c,'get_json',side_effect=fake):
            rows = c.jobs_greenhouse('Acme','acme',1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['date_source'],'first_published')

    def test_all_eight_platforms_accept_today_only(self):
        stamp='2026-11-20T17:00:00Z'
        gh_list={'jobs':[{'title':'Software Engineer','id':1,'location':{'name':'Remote - US'}}]}
        gh_detail={'title':'Software Engineer','id':1,'location':{'name':'Remote - US'},
                   'first_published':stamp, 'absolute_url':'https://example.com/gh'}
        responses={
            'ashby':{'jobs':[{'title':'AI Engineer','location':'Remote - UK',
                 'workplaceType':'Remote','publishedAt':stamp,'jobUrl':'https://example.com/as'}]},
            'workable':{'jobs':[{'title':'Python Developer','locations':[{'country':'United States', 'city':'Remote'}],
                 'workplace_type':'remote','published_on':'2026-11-20','shortcode':'w1'}]},
            'smartrecruiters':{'content':[{'name':'Data Engineer',
                 'location':{'country':'United Kingdom','remote':True},'releasedDate':stamp,'id':'s1'}],
                 'totalFound':1},
            'recruitee':{'offers':[{'title':'ML Engineer','country':'United States','remote':True,
                 'published_at':stamp,'id':'r1'}]},
            'breezy':[{'name':'MLOps Engineer','location':{'country':'United Kingdom','is_remote':True},
                 'published_date':stamp,'id':'b1'}],
            'wdlist':{'jobPostings':[{'title':'Platform Engineer','externalPath':'/job/test',
                 'locationsText':'Remote - US'}], 'total':1},
            'wddetail':{'jobPostingInfo':{'startDate':'2026-11-20', 'workplaceType':'Remote',
                 'location':'Remote - US','jobReqId':'wd1','jobDescription':'Remote position'}},
            'bamboolist':{'result':[{'id':12,'jobOpeningName':'AI Trainer','location':{'country':'United States'}}]},
            'bamboodetail':{'result':{'jobOpening':{'datePosted':'2026-11-20','isRemote':True,
                 'description':'Fully remote'}}},
        }
        def fake(url,payload=None):
            if 'boards-api.greenhouse.io' in url:return gh_detail if '/jobs/1' in url else gh_list
            if 'api.ashbyhq.com' in url:return responses['ashby']
            if 'apply.workable.com' in url:return responses['workable']
            if 'api.smartrecruiters.com' in url:return responses['smartrecruiters']
            if 'recruitee.com' in url:return responses['recruitee']
            if 'breezy.hr' in url:return responses['breezy']
            if 'myworkdayjobs.com' in url:return responses['wddetail'] if '/job/test' in url else responses['wdlist']
            if 'bamboohr.com' in url:return responses['bamboodetail'] if '/detail' in url else responses['bamboolist']
            raise AssertionError(url)
        boards=[('greenhouse','A','x'),('ashby','A','x'),('workable','A','x'),
                ('smartrecruiters','A','x'),('recruitee','A','x'),('breezy','A','x'),
                ('workday','A','https://acme.wd5.myworkdayjobs.com/External'),('bamboohr','A','x')]
        with patch.object(c,'get_json',side_effect=fake):
            for b in boards:
                with self.subTest(platform=b[0]):
                    rows, error=c.process_board(b,1)
                    self.assertIsNone(error)
                    self.assertEqual(len(rows),1)
                    self.assertEqual(rows[0]['published_date_pt'],'2026-11-20')
                    self.assertIn(rows[0]['country'],('US','UK','US & UK'))

    def test_ashby_last_published_and_hybrid(self):
        base = {'title':'AI Engineer','jobUrl':'https://jobs.ashbyhq.com/acme/uuid',
                'location':'Remote - United Kingdom',
                'publishedAt':'2026-11-20T20:00:00Z','workplaceType':'Remote'}
        self.assertEqual(c.normalize_ashby('Acme','acme',base,1)[0]['published_date_pt'],'2026-11-20')
        base['workplaceType']='Hybrid'
        self.assertEqual(c.normalize_ashby('Acme','acme',base,1),[])

class CombineAndHistoryTests(unittest.TestCase):
    def test_no_missing_shards_and_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd=os.getcwd()
            try:
                os.chdir(tmp)
                Path('shards/ats-shard-0').mkdir(parents=True)
                row = dict.fromkeys(c.FIELDS,'')
                row.update(platform='Greenhouse',company='Company',title='AI Engineer',
                           country='UK',published_date_pt='2026-11-20',
                           published_utc='2026-11-20T17:00:00Z', job_id='13',board_slug='acme',
                           date_source='first_published',location='Remote - UK')
                with self.assertRaises(FileNotFoundError):
                    combine.combine(expected=2)
                for i in (0,1):
                    p=Path(f'shards/ats-shard-{i}')
                    p.mkdir(parents=True,exist_ok=True)
                    for suffix in ('us_uk_remote','review_location','remote_candidates'):
                        with (p/f'jobs_shard_{i}_{suffix}.csv').open('w',newline='') as f:
                            w=csv.DictWriter(f,fieldnames=c.FIELDS)
                            w.writeheader()
                            if suffix != 'review_location':w.writerow(row)
                summary=combine.combine(expected=2)
                self.assertEqual(summary['confirmed_today'],1)
                merge.main()
                out=merge.read_rows(Path('jobs_all.csv'))
                self.assertEqual(len(out),1)
                self.assertEqual(out[0]['collection_version'],merge.VERSION)
            finally:
                os.chdir(cwd)

if __name__=='__main__':
    unittest.main()
