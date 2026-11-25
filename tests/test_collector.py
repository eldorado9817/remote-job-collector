import csv
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import discover_boards as discovery
import collect_remote_jobs as collector


class DirectoryTests(TestCase):
    def test_storesignal_all_expected_platforms(self):
        rows = discovery.parse_storesignal(
            "ats,slug,company_name,open_jobs\n"
            "greenhouse,stripe,Stripe,3\n"
            "smartrecruiters,BoschGroup,Bosch,4\n"
            "recruitee,bunq,Bunq,5\n"
            "workable,huggingface,HuggingFace,6\n")
        self.assertEqual(len(rows), 4)
        self.assertIn(('smartrecruiters', 'boschgroup'), rows)

    def test_riley_workday_triples_are_urls(self):
        rows = discovery.parse_riley(json.dumps(["nvidia|wd5|NVIDIAExternalCareerSite", "wd5|wd2|fake", "not-a-real-url"]), "workday")
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows.values())[0][2], 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite')

    def test_riley_bamboo_subdomains(self):
        rows = discovery.parse_riley(json.dumps(["acme", "example"]), "bamboohr")
        self.assertEqual(len(rows), 2)

    def test_openats_mixed_dict(self):
        rows = discovery.parse_openats(json.dumps({"breezy": ["startup", "studio"], "recruitee": ["agency"]}))
        self.assertEqual(len(rows), 3)
        self.assertIn(('breezy', 'startup'), rows)

    def test_real_openats_live_pairs(self):
        payload = {"as_of": "2026-09-03", "providers": {
            "breezy": {"live": [["samplecompany", 7], ["another", 2]]},
            "recruitee": {"live": [["recruit", 5]]},
            "teamtailor": {"live": [["skip", 7]]}}}
        rows = discovery.parse_openats(json.dumps(payload))
        self.assertEqual(len(rows), 3)
        self.assertIn(("breezy", "samplecompany"), rows)

    def test_discovery_sources_merged_with_failure_reporting(self):
        fixture = {
            'boards.csv': 'ats,slug,company_name\nsmartrecruiters,Example,Test Company\n',
            'workday_companies.json': '["example|wd5|Careers"]',
            'bamboohr_companies.json': '["staffing"]',
            'companies.json': '{"breezy":["startup"]}',
        }
        def fake_fetch(url):
            for needle, value in fixture.items():
                if url.endswith('/' + needle):
                    return value
            raise ValueError(url)
        with patch.dict(discovery.SOURCES, {k:'https://example.com/'+u.split('/')[-1] for k,u in discovery.SOURCES.items()}, clear=True):
            boards = discovery.build(fetcher=fake_fetch, extra_path=None, seed_paths=())
        self.assertEqual(len(boards), 4)


class JobTests(TestCase):
    def setUp(self):
        self.prev = collector.SETTINGS.copy()
        collector.SETTINGS.update(scope='worldwide', roles='all', workplace='any', allow_undated=False)

    def tearDown(self):
        collector.SETTINGS.clear()
        collector.SETTINGS.update(self.prev)

    def test_worldwide_accepts_any_role_and_location(self):
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        rows = collector.normalize_generic('SmartRecruiters', 'Acme', 'acme', {
            'title':'Marketing Manager', 'location':'Berlin, Germany',
            'date':now, 'url':'https://example.com/99', 'id':'99', 'date_source':'releasedDate'}, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Marketing Manager')

    def test_remote_usuk_original_filter_still_applies(self):
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        collector.SETTINGS.update(scope='usuk', roles='engineering', workplace='remote')
        rows = collector.normalize_generic('Breezy HR', 'X', 'x', {
            'title':'Senior Software Engineer', 'location':'Remote - US', 'remote':True,
            'date':now, 'id':'1'}, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['country'], 'US')

    def test_undated_labels_unknown_not_new(self):
        collector.SETTINGS['allow_undated'] = True
        rows = collector.normalize_generic('BambooHR', 'Example', 'example', {
            'title':'UX Designer', 'location':'Paris', 'id':42}, 1)
        self.assertEqual(rows[0]['date_source'], 'unavailable')
        self.assertEqual(rows[0]['published_utc'], '')

    def test_workable_feed_streaming(self):
        xml = b'<source><job><title>Designer</title><date>Fri, 20 Nov 2026 08:00:00 UTC</date><referencenumber>A1</referencenumber><url>https://apply.workable.com/j/A1</url><company>Acme</company><city>London</city><country>UK</country><remote>true</remote></job></source>'
        class Fake:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n=-1): return self.buf.read(n)
            def __init__(self): self.buf=io.BytesIO(xml)
            def close(self): pass
        with patch.object(collector, 'urlopen', return_value=Fake()):
            rows = collector.jobs_workable_global(3650)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['platform'], 'Workable')
        self.assertEqual(rows[0]['job_id'], 'A1')


    def test_rippling_groups_locations_and_reads_detail(self):
        collector.SETTINGS.update(scope='usuk', roles='software-ai', workplace='remote')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        listing = [
            dict(uuid='u1', name='Senior Software Engineer', url='https://ats.rippling.com/acme/jobs/u1',
                 workLocation={'label': 'New York, NY'}),
            dict(uuid='u1', name='Senior Software Engineer', url='https://ats.rippling.com/acme/jobs/u1',
                 workLocation={'label': 'Remote (United States)'}),
            dict(uuid='u2', name='Account Executive', workLocation={'label': 'Remote (United States)'}),
            dict(uuid='u3', name='ML Engineer', workLocation={'label': 'Hybrid (London, UK)'}),
        ]
        details = {
            'u1': dict(uuid='u1', name='Senior Software Engineer', companyName='Acme Inc',
                       workLocations=['New York, NY', 'Remote (United States)'], createdOn=now,
                       description={'role': '<p>Build things</p>', 'company': 'About Acme'},
                       url='https://ats.rippling.com/acme/jobs/u1'),
            'u3': dict(uuid='u3', name='ML Engineer', workLocations=['Hybrid (London, UK)'], createdOn=now),
        }
        calls = []
        def fake_get_json(url, payload=None):
            calls.append(url)
            if url.endswith('/board/acme/jobs'):
                return listing
            return details[url.rsplit('/', 1)[-1]]
        with patch.object(collector, 'get_json', side_effect=fake_get_json):
            rows, err = collector.process_board(('rippling', 'acme', 'acme'), 1)
        self.assertIsNone(err)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['platform'], 'Rippling')
        self.assertEqual(rows[0]['company'], 'Acme Inc')
        self.assertEqual(rows[0]['country'], 'US')
        self.assertEqual(rows[0]['date_source'], 'createdOn')
        # One detail call per matching uuid; non-software title never fetched.
        self.assertEqual(sorted(c.rsplit('/', 1)[-1] for c in calls[1:]), ['u1', 'u3'])

    def test_lever_workplace_and_eu_fallback(self):
        from urllib.error import HTTPError
        collector.SETTINGS.update(scope='usuk', roles='software-ai', workplace='remote')
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        postings = [
            dict(id='a', text='Backend Engineer', workplaceType='remote', createdAt=now_ms, country='GB',
                 categories={'location': 'London', 'allLocations': ['London']},
                 hostedUrl='https://jobs.lever.co/acme/a'),
            dict(id='b', text='Backend Engineer', workplaceType='hybrid', createdAt=now_ms,
                 categories={'location': 'Remote - US'}),
            dict(id='c', text='Recruiter', workplaceType='remote', createdAt=now_ms,
                 categories={'location': 'Remote - US'}),
            dict(id='d', text='AI Engineer', workplaceType='remote', createdAt=now_ms - 5 * 86400000,
                 categories={'location': 'Remote - US'}),
        ]
        def fake_get_json(url, payload=None):
            if 'api.lever.co' in url:
                raise HTTPError(url, 404, 'Not Found', None, None)
            self.assertIn('api.eu.lever.co', url)
            return postings
        with patch.object(collector, 'get_json', side_effect=fake_get_json):
            rows, err = collector.process_board(('lever', 'Acme', 'acme'), 1)
        self.assertIsNone(err)
        self.assertEqual([r['job_id'] for r in rows], ['a'])
        self.assertEqual(rows[0]['country'], 'UK')
        self.assertEqual(rows[0]['date_source'], 'createdAt')

    def test_rippling_seed_rows_are_discovered(self):
        out = {}
        discovery.add(out, 'rippling', 'Acme', 'acme-careers')
        discovery.add(out, 'rippling', 'Bad', '../etc')
        self.assertEqual(list(out), [('rippling', 'acme-careers')])


if __name__ == '__main__':
    import unittest
    unittest.main()


class USUKRemoteJobTests(TestCase):
    """Regression tests for desired all-roles / remote / US-UK settings."""
    def setUp(self):
        self.prev = collector.SETTINGS.copy()
        collector.SETTINGS.update(
            scope='usuk', roles='all', workplace='remote',
            allow_undated=True, all_dates=True,
        )

    def tearDown(self):
        collector.SETTINGS.clear()
        collector.SETTINGS.update(self.prev)

    def test_non_engineering_old_and_undated_uk_us_jobs(self):
        samples = [
            dict(title='Sales Representative', location='London, United Kingdom - Remote',
                 date='2023-01-01', id='A'),
            dict(title='Marketing Director', location='Remote - United States',
                 date='', id='B'),
        ]
        rows = [r for job in samples for r in collector.normalize_generic('Test ATS','A','a',job,1)]
        self.assertEqual({r['title'] for r in rows}, {'Sales Representative', 'Marketing Director'})
        self.assertEqual({r['country'] for r in rows}, {'US','UK'})
        self.assertEqual({r['date_source'] for r in rows}, {'unknown (date only)','unavailable'})

    def test_hybrid_and_onsite_are_rejected_even_with_remote_true(self):
        for structured in ('hybrid', 'on-site', 'onsite'):
            rows = collector.normalize_generic('X','X','X',dict(
                title='Staff Accountant', location='Remote - US', remote=True,
                workplace=structured, date='2024-01-01', id=structured),1)
            self.assertEqual(rows, [], structured)
        self.assertEqual(collector.normalize_generic('X','X','X',dict(
            title='Sales Manager', location='Hybrid - London UK',date='2024-01-01',id=1),1),[])

    def test_remote_without_country_is_for_manual_review(self):
        rows = collector.normalize_generic('X','X','X',dict(
            title='Content Writer', location='Remote', date='2024-01-01', id=10),1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['country'].startswith('Review'))
        self.assertEqual(collector.geography(['Remote — Worldwide'], ''),'US & UK')

    def test_all_eight_platform_adapters_with_fixture_responses(self):
        """Every adapter should admit non-engineering remote US/UK postings."""
        gh = dict(jobs=[dict(title='Marketing Manager',
            location={'name':'Remote, United States'},
            first_published='2023-01-01T12:00:00Z',
            id=1, absolute_url='https://example.com/1')])
        ashby = dict(jobs=[dict(title='HR Business Partner', location='Remote - UK',
            workplaceType='Remote', publishedAt='2024-01-01T12:00:00Z',
            isListed=True, jobUrl='https://jobs.ashbyhq.com/acme/2')])
        workable = dict(jobs=[dict(title='Sales', locations=[{'country':'United States',
             'city':'Remote'}], workplace_type='remote', published_on='2024-01-01',
             shortcode='3',url='https://example.com/3')])
        sr = dict(content=[dict(name='Finance Analyst', location={'country':'United Kingdom',
            'remote':True, 'locationType':'REMOTE'}, releasedDate='2024-01-01T12:00:00Z',id='4')],totalFound=1)
        recruitee = dict(offers=[dict(title='Designer',country='US', remote=True,
            published_at='2024-01-01T12:00:00Z',id=5,careers_url='https://example.com/5')])
        breezy = [dict(name='Customer Success Manager',location={'country':'United Kingdom',
            'is_remote':True},published_date='2024-01-01T12:00:00Z', id=6,url='https://example.com/6')]
        wdlist = dict(jobPostings=[dict(title='Product Manager', externalPath='/job/abc',
            locationsText='United Kingdom')],total=1)
        wddetail = dict(jobPostingInfo={'startDate':'2024-01-01',
            'workplaceType':'Remote','location':'United Kingdom',
            'jobReqId':'WD-7', 'jobDescription':'Remote role'})
        bamboolist = dict(result=[dict(id=8, jobOpeningName='Operations Manager',
            location={'country':'United States'})])
        bamboodetail = dict(result={'jobOpening':{'datePosted':'2024-01-01',
            'isRemote':True, 'description':'Fully remote'}})
        def fake_get_json(url, payload=None):
            if 'boards-api.greenhouse.io' in url: return gh['jobs'][0] if '/jobs/1' in url else gh
            if 'api.ashbyhq.com' in url: return ashby
            if 'apply.workable.com' in url: return workable
            if 'api.smartrecruiters.com' in url: return sr
            if 'recruitee.com' in url: return recruitee
            if 'breezy.hr' in url: return breezy
            if 'myworkdayjobs.com' in url: return wddetail if '/job/abc' in url else wdlist
            if 'bamboohr.com' in url: return bamboodetail if '/detail' in url else bamboolist
            raise AssertionError('Unexpected URL '+url)
        boards = [
            ('greenhouse','GH','example'),('ashby','Ashby','example'),
            ('workable','Workable','example'),('smartrecruiters','SR','example'),
            ('recruitee','Recruitee','example'),('breezy','Breezy','example'),
            ('workday','Workday','https://acme.wd5.myworkdayjobs.com/External'),
            ('bamboohr','BambooHR','example'),
        ]
        with patch.object(collector,'get_json',side_effect=fake_get_json):
            for board in boards:
                with self.subTest(ats=board[0]):
                    rows, err = collector.process_board(board, 1)
                    self.assertIsNone(err)
                    self.assertEqual(len(rows),1)
                    self.assertIn(rows[0]['country'],('US','UK','US & UK'))


class MigrationTests(TestCase):
    def test_previous_worldwide_results_are_excluded_from_cumulative(self):
        import os
        import merge_job_results as merger
        cwd=os.getcwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                def add_csv(path, rows, fields):
                    with open(path,'w',encoding='utf-8',newline='') as f:
                        writer=csv.DictWriter(f,fieldnames=fields)
                        writer.writeheader();writer.writerows(rows)
                add_csv('jobs_all.csv',[
                    dict(platform='Greenhouse',company='Old',title='On-site HR', country='US',job_id='old',board_slug='old',location_check='Worldwide search; verify employer location eligibility', first_seen_utc='2024-01-01'),
                    dict(platform='Ashby',company='Old',title='Remote Writer',country='UK',job_id='keep',board_slug='keep',location_check='Location indicated: US/UK', first_seen_utc='2024-02-01'),
                ],merger.FIELDS)
                add_csv('jobs_us_uk_remote.csv',[
                    dict(platform='Breezy HR',company='New',title='Marketing',country='US',job_id='new',board_slug='new',date_source='unavailable')
                ],collector.FIELDS)
                merger.main()
                rows=merger.read_rows(Path('jobs_all.csv'))
                self.assertEqual(rows, [], 'Legacy all-role/undated rows must not be migrated into the strict software+AI published-day history')
            finally:
                os.chdir(cwd)
