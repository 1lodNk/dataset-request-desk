import io
from .conftest import add_user, token_for
from app.models import Episode

def csv_bytes():
    return b"episode_id,robot_id,task_name,recorded_at,duration_seconds,operator_name,quality\nEP-1,arm-01, Pick Cup ,2026-08-01T10:00:00,30,Aline,GOOD\nEP-1,arm-01,pick cup,2026-08-01T10:00:00,30,Aline,good\nEP-2,unknown,pick cup,2026-08-01T10:00:00,30,Aline,good\nEP-3,arm-02,pick cup,2026-08-01T10:00:00,-4,Aline,good\n"

def test_import_is_idempotent_and_reports_invalid(client, db):
    op=add_user(db,'op@example.com','operator'); headers={'Authorization':f'Bearer {token_for(client,op.email)}'}
    first=client.post('/episodes/import',headers=headers,files={'file':('episodes.csv',io.BytesIO(csv_bytes()),'text/csv')}).json()
    second=client.post('/episodes/import',headers=headers,files={'file':('episodes.csv',io.BytesIO(csv_bytes()),'text/csv')}).json()
    assert first['imported'] == 1
    assert first['duplicates'] == 1
    assert first['invalid'] == 2
    assert second['imported'] == 0
    assert second['duplicates'] == 2
    assert db.query(Episode).count() == 1
    episode=db.query(Episode).one()
    assert episode.task_name == 'pick cup'
    assert episode.quality == 'good'
