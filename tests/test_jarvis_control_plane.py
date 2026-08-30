"""End-to-end contracts behind the JARVIS control plane."""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from gateway.gateway import app


def test_jarvis_page_dependency_order_and_no_fake_claims():
    text = TestClient(app).get('/jarvis').text
    assert text.index('hermus-client.js') < text.index('jarvis-control.js') < text.index('class HermusAudioEngine')
    assert '318 Test Suites Verified Passing' not in text
    assert 'Page scrape active' not in text
    assert 'Autonomy: 100%' not in text
    assert 'const isUiCommand = val.startsWith' in text
    assert 'await jarvisRun(raw, files)' in text
    assert 'Stop backend run' in text


def test_jarvis_status_is_real_aggregate_and_secret_free():
    with TestClient(app) as client:
        response = client.get('/api/jarvis/status')
    assert response.status_code == 200
    body = response.json()
    assert body['gateway']['reachable'] is True
    assert isinstance(body['gateway']['uptime_seconds'], int)
    assert {'enabled', 'started', 'by_status'} <= body['queue'].keys()
    assert {'active_jobs', 'active_runs', 'tools', 'agents', 'artifacts'} <= body['counts'].keys()
    assert 'telemetry' in body and 'pid' in body['telemetry']
    # Aggregate does not include the key registry or raw credential fields.
    assert 'keys' not in body
    assert 'llm_keys' not in body


def test_navigator_rejects_non_http_and_private_targets():
    with TestClient(app) as client:
        for url in ('file:///etc/passwd', 'http://127.0.0.1:8000/api/status'):
            response = client.post('/navigator/fetch', json={'url': url})
            assert response.status_code == 400
            assert response.json()['success'] is False


def test_cancel_unknown_run_is_honest():
    with TestClient(app) as client:
        response = client.post('/run/cancel/run_missing_for_jarvis')
    assert response.status_code == 404
    assert response.json()['cancelled'] is False


def test_cancel_active_run_reaches_backend():
    from core.run_events import run_bus
    run_id = 'run_jarvis_cancel_contract'
    run_bus.start(run_id, label='test')
    try:
        with TestClient(app) as client:
            response = client.post(f'/run/cancel/{run_id}')
        assert response.status_code == 200
        assert response.json()['cancelled'] is True
        assert run_bus.is_cancelled(run_id) is True
    finally:
        run_bus.finish(run_id, 'cancelled')


def test_queue_command_can_be_observed_and_result_render_contract(monkeypatch):
    """Real HTTP -> queue -> runtime -> status -> result lifecycle, with only
    the model itself replaced so the test is deterministic and offline."""
    class Agent:
        mode = SimpleNamespace(value='agent')
        mode_config = SimpleNamespace(name='Agent', description='test')
        model_name = 'test/model'
        profile = ''
        project = 'default'
        llm = SimpleNamespace(provider='test', _resolve_bundle=lambda: {'provider': 'test'})
        def chat(self, text, **kwargs):
            return {'response': 'real queue result: ' + text, 'steps': 1, 'tool_calls': []}

    monkeypatch.setattr('gateway.gateway.get_agent_for_user', lambda *a, **k: Agent())
    # The queue handler's injected getter is set during lifespan startup.
    with TestClient(app) as client:
        submitted = client.post('/command', json={
            'text': 'Hello Jarvis', 'platform': 'jarvis', 'user_id': 'smoke',
            'run_id': 'run_jarvis_queue_smoke', 'async': True, 'stream': True,
        })
        assert submitted.status_code == 200
        accepted = submitted.json()
        assert accepted['async'] is True and accepted['run_id']
        job_id = accepted['job_id']
        import time
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            status = client.get(f'/jobs/{job_id}').json()
            if status.get('status') in {'succeeded', 'failed', 'cancelled'}:
                break
            time.sleep(.05)
        assert status['status'] == 'succeeded', status
        result = client.get(f'/jobs/{job_id}/result')
        assert result.status_code == 200
        assert 'Hello Jarvis' in result.json()['result']['response']
        events = client.get(f'/jobs/{job_id}/events?follow=false').json()['events']
        assert any(event['type'] == 'run_finished' for event in events)


def test_javascript_sources_parse_with_node():
    """A repeatable browser-init smoke prerequisite (syntax/runtime assets)."""
    import shutil, subprocess, tempfile
    if not shutil.which('node'):
        return
    subprocess.run(['node', '--check', 'gateway/static/hermus-client.js'], check=True)
    subprocess.run(['node', '--check', 'gateway/static/jarvis-control.js'], check=True)
    subprocess.run(['node', '--test', 'tests/js/jarvis-client.test.js'], check=True)
    html = Path('gateway/jarvis_dashboard.html').read_text()
    inline = html.split('<script>')[1].split('</script>', 1)[0]
    with tempfile.NamedTemporaryFile('w', suffix='.js') as fh:
        fh.write(inline); fh.flush()
        subprocess.run(['node', '--check', fh.name], check=True)
