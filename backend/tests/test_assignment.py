from datetime import datetime, timezone, date
from .conftest import add_user, token_for
from app.models import Episode

def test_only_good_or_usable_and_episode_is_single_use(client, db):
    c=add_user(db,'client@example.com','client'); op=add_user(db,'op@example.com','operator')
    headers={'Authorization':f'Bearer {token_for(client,op.email)}'}
    r1=client.post('/requests',headers={'Authorization':f'Bearer {token_for(client,c.email)}'},json={'task_name':'pick cup','episodes_requested':1,'deadline':str(date.today())}).json()
    r2=client.post('/requests',headers={'Authorization':f'Bearer {token_for(client,c.email)}'},json={'task_name':'pick cup','episodes_requested':1,'deadline':str(date.today())}).json()
    bad=Episode(episode_id='EP-BAD',robot_id='arm-01',task_name='pick cup',recorded_at=datetime.now(timezone.utc),duration_seconds=20,quality='bad')
    good=Episode(episode_id='EP-GOOD',robot_id='arm-01',task_name='pick cup',recorded_at=datetime.now(timezone.utc),duration_seconds=20,quality='good')
    db.add_all([bad,good]);db.commit();db.refresh(bad);db.refresh(good)
    assert client.post(f'/episodes/{bad.id}/assign/{r1["id"]}',headers=headers).status_code == 400
    assert client.post(f'/episodes/{good.id}/assign/{r1["id"]}',headers=headers).status_code == 204
    assert client.post(f'/episodes/{good.id}/assign/{r2["id"]}',headers=headers).status_code == 409
