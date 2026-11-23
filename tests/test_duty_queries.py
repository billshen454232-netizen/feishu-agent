import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app import _add_duty_routes
from src.duty_queries import DutyQueries, resolve_day
from src.event_handler import FeishuEventHandler
from src.team_kb_questions import TeamKbQuestionStore


class FeishuStub:
    def __init__(self):
        self.replies = []

    async def reply_text(self, message_id, text):
        self.replies.append((message_id, text))


def test_duty_reply_is_bound_to_original_message_in_each_bot_group(tmp_path):
    store = TeamKbQuestionStore(tmp_path / 'duty.sqlite3')
    store.initialize()
    feishu = FeishuStub()
    config = SimpleNamespace(feishu=SimpleNamespace(verification_token='verify-fixture'))
    duty = DutyQueries(store, feishu, token='duty-fixture', event_token=config.feishu.verification_token,
                       bot_open_id='ou_bot')
    app = FastAPI()
    handler = FeishuEventHandler(config, None, None, duty_queries=duty)
    _add_duty_routes(app, duty)

    @app.post('/feishu/events')
    async def on_event(payload: dict):
        return await handler.handle(payload)

    def event(message_id, *, chat='oc_allowed', kind='group', mention='ou_bot', token='verify-fixture'):
        return {'header': {'event_id': 'evt_fixture', 'event_type': 'im.message.receive_v1', 'token': token},
                'event': {'message': {'chat_type': kind, 'chat_id': chat, 'message_id': message_id, 'message_type': 'text',
                                      'content': json.dumps({'text': '@_user_1 2026-11-23 谁值班？'}),
                                      'mentions': [{'key': '@_user_1', 'id': {'open_id': mention}}]}}}

    with TestClient(app) as client:
        for item in (event('om_wrong_mention', mention='ou_other'), event('om_bad_token', token='wrong'),
                     event('om_private', kind='p2p')):
            # DMs contain just the question text.
            if item['event']['message']['chat_type'] == 'p2p':
                item['event']['message']['content'] = json.dumps({'text': '2026-11-23 谁值班？'})
            assert client.post('/feishu/events', json=item).status_code == 200
        headers = {'Authorization': 'Bearer duty-fixture'}
        assert client.post('/internal/worker/duty/claim').status_code == 401
        assert client.post('/internal/worker/duty/claim', headers=headers).json() == {'items': []}
        assert client.post('/feishu/events', json=event('om_duty')).status_code == 200
        assert client.post('/feishu/events', json=event('om_duty')).status_code == 200
        assert store.claim() == []
        assert client.post('/internal/worker/duty/claim', headers=headers).json() == {
            'items': [{'id': 'om_duty', 'date': '2026-11-23'}],
        }
        result = {'status': 'found', 'people': ['示例同学'], 'source': '示例值班表'}
        route = '/internal/worker/duty/om_duty/answer'
        assert client.post(route, headers=headers, json={'status': 'found'}).status_code == 422
        assert client.post(route, headers=headers, json=result).status_code == 200
        assert client.post(route, headers=headers, json=result).status_code == 404
        assert store.get('om_duty')['answer'] == ''
        assert client.post('/internal/worker/duty/claim', headers=headers).json() == {'items': []}

        assert client.post('/feishu/events', json=event('om_other_group', chat='oc_other')).status_code == 200
        assert client.post('/internal/worker/duty/claim', headers=headers).json() == {
            'items': [{'id': 'om_other_group', 'date': '2026-11-23'}],
        }
        assert client.post('/internal/worker/duty/om_other_group/answer', headers=headers, json=result).status_code == 200

    expected = '2026-11-23 值班人员：示例同学\n来源：示例值班表'
    assert feishu.replies == [('om_duty', expected), ('om_other_group', expected)]


def test_relative_duty_day_resolves_in_beijing():
    instant = datetime(2026, 11, 23, 17, tzinfo=timezone.utc)
    assert resolve_day('今天', instant).isoformat() == '2026-11-24'
    assert resolve_day('明天', instant).isoformat() == '2026-11-25'
    assert resolve_day('2026年11月29日', instant).isoformat() == '2026-11-29'
