from datetime import date
from .conftest import add_user, token_for

def test_client_cannot_see_another_clients_request(client, db):
    a = add_user(db, 'a@example.com', 'client')
    b = add_user(db, 'b@example.com', 'client')
    token_a = token_for(client, a.email)
    token_b = token_for(client, b.email)
    created = client.post('/requests', headers={'Authorization': f'Bearer {token_a}'}, json={'task_name':'pick cup','episodes_requested':2,'deadline':str(date.today())}).json()
    assert client.get(f"/requests/{created['id']}", headers={'Authorization': f'Bearer {token_b}'}).status_code == 404

def test_client_cannot_assign_episode(client, db):
    user = add_user(db, 'client@example.com', 'client')
    token = token_for(client, user.email)
    assert client.get('/episodes', headers={'Authorization': f'Bearer {token}'}).status_code == 403
