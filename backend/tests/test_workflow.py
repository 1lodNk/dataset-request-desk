from datetime import date
from .conftest import add_user, token_for
from app.models import Episode

def auth(client, email):
    return {'Authorization': f'Bearer {token_for(client, email)}'}

def make_request(client, db, client_user, count=1):
    return client.post('/requests', headers=auth(client, client_user.email), json={'task_name':'pick cup','episodes_requested':count,'deadline':str(date.today())}).json()

def make_episode(db, episode_id='EP-1', quality='good'):
    from datetime import datetime, timezone
    e=Episode(episode_id=episode_id,robot_id='arm-01',task_name='pick cup',recorded_at=datetime.now(timezone.utc),duration_seconds=30,operator_name='Op',quality=quality)
    db.add(e);db.commit();db.refresh(e);return e

def test_delivered_requires_enough_episodes(client, db):
    c=add_user(db,'client@example.com','client'); op=add_user(db,'op@example.com','operator')
    req=make_request(client,db,c,2); ep=make_episode(db)
    assert client.post(f"/episodes/{ep.id}/assign/{req['id']}",headers=auth(client,op.email)).status_code == 204
    assert client.post(f"/requests/{req['id']}/status",headers=auth(client,op.email),json={'status':'in_progress'}).status_code == 200
    response=client.post(f"/requests/{req['id']}/status",headers=auth(client,op.email),json={'status':'delivered'})
    assert response.status_code == 400

def test_valid_transition_and_client_acceptance(client, db):
    c=add_user(db,'client@example.com','client'); op=add_user(db,'op@example.com','operator')
    req=make_request(client,db,c,1); ep=make_episode(db)
    assert client.post(f"/episodes/{ep.id}/assign/{req['id']}",headers=auth(client,op.email)).status_code == 204
    assert client.post(f"/requests/{req['id']}/status",headers=auth(client,op.email),json={'status':'in_progress'}).status_code == 200
    assert client.post(f"/requests/{req['id']}/status",headers=auth(client,op.email),json={'status':'delivered'}).status_code == 200
    assert client.post(f"/requests/{req['id']}/status",headers=auth(client,c.email),json={'status':'accepted'}).status_code == 200

def test_invalid_transition_rejected(client, db):
    c=add_user(db,'client@example.com','client'); op=add_user(db,'op@example.com','operator')
    req=make_request(client,db,c)
    assert client.post(f"/requests/{req['id']}/status",headers=auth(client,op.email),json={'status':'accepted'}).status_code == 403
