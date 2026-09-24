from buildflow.web import create_app
from html.parser import HTMLParser
from pathlib import Path


def test_landing_handoff_includes_complete_prompt_and_keeps_workbench_accessible():
    class Page(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_prompt = False
            self.prompt = ''
            self.links = []
            self.inputs = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'textarea' and attrs.get('id') == 'normalizerPrompt':
                self.in_prompt = True
            if tag == 'a':
                self.links.append(attrs)
            if tag == 'input':
                self.inputs.append(attrs.get('name'))

        def handle_endtag(self, tag):
            if tag == 'textarea':
                self.in_prompt = False

        def handle_data(self, data):
            if self.in_prompt:
                self.prompt += data

    client = create_app().test_client()
    for url in ['/', '/normalize']:
        page = Page()
        page.feed(client.get(url).get_data(as_text=True))
        expected = (Path(__file__).parents[1] / 'docs/normalization/CHAT_NORMALIZER_PROMPT.md').read_text()
        assert page.prompt == expected
        providers = [a for a in page.links if 'data-provider' in a]
        assert {a['href'] for a in providers} == {'https://chatgpt.com/', 'https://claude.ai/new', 'https://gemini.google.com/app'}
        assert all(a.get('target') == '_blank' and 'noopener' in a.get('rel', '') for a in providers)
        assert {'originals', 'normalized'} <= set(page.inputs)
        assert any(a.get('href') == '/workbench' for a in page.links)
    assert client.get('/workbench').status_code == 200


def test_health_and_sample_analysis_and_export():
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    assert client.get("/api/health").get_json()["status"] == "ok"
    response = client.post(
        "/api/analyze",
        data={
            "sample_id": "rwh",
            "classifier_mode": "hybrid",
            "cashflow_mode": "compare",
            "indirect_cost_pct": "5",
            "retention_pct": "5",
            "payment_lag_months": "1",
        },
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["typology"] == "rwh"
    export = client.get(f"/api/jobs/{result['job_id']}/export.xlsx")
    assert export.status_code == 200
    assert export.data[:2] == b"PK"


def test_schedule_page_and_api_return_chart_ready_data():
    client = create_app().test_client()
    page = client.get('/schedule')
    assert page.status_code == 200 and b'ganttBox' in page.data and b'/static/schedule.js' in page.data
    demo = client.get('/api/schedule/demo?start=2026-10-01').get_json()
    assert demo['project_duration_days'] == 114 and demo['project_start_date'] == '2026-10-01'
    a = demo['activities'][0]
    assert {'start_date', 'end_date', 'duration_days', 'predecessors', 'critical', 'cost', 'monthly_cost'} <= set(a)
    assert demo['monthly'][-1]['cumulative_percent'] == 100.0
    source = client.get('/api/schedule/demo-input').get_json()
    posted = client.post('/api/schedule/run', json={'activities_json': source, 'start_date': '2026-11-02',
                                                    'cashflow': {'retention_pct': 5}})
    assert posted.status_code == 200 and posted.get_json()['project_start_date'] == '2026-11-02'
    assert client.post('/api/schedule/run', json={}).status_code == 400
    bad = client.post('/api/schedule/run', json={'activities_json': {'activities': [
        {'activity_id': 'A', 'activity_class': 'NOPE', 'quantity': 1, 'predecessors': []}]}, 'start_date': '2026-10-01'})
    assert bad.status_code == 400 and 'duration' in bad.get_json()['error']


def test_two_tank_demo_payload_runs_through_the_pipeline():
    client = create_app().test_client()
    demo = client.get('/api/schedule/demo-two-tank').get_json()
    result = client.post('/api/schedule/run', json={'activities_json': demo['input'], 'overrides': demo['overrides'],
                                                    'start_date': demo['start_date']}).get_json()
    assert result['project_start_date'] == '2026-04-25' and result['project_duration_days'] == 75
    assert len(result['activities']) == 40
