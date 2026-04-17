from fastapi.testclient import TestClient
import sys
sys.path.append('.')

from main import app
from database import get_db
from models import Assignment, TrainingStatus
from auth import create_access_token

client = TestClient(app)

def find_completed_assignment(db):
    return db.query(Assignment).filter(Assignment.status == TrainingStatus.completed).first()


def main():
    db = next(get_db())
    assignment = find_completed_assignment(db)
    if not assignment:
        print('No completed assignments found in DB')
        return

    user = assignment.user
    username = user.username
    role = user.role.value if hasattr(user.role, 'value') else str(user.role)

    token = create_access_token({"sub": username, "role": role})
    headers = {"Authorization": f"Bearer {token}"}

    url = f"/assignments/{assignment.id}/certificate"
    resp = client.get(url, headers=headers)

    print('Request URL:', url)
    print('Status code:', resp.status_code)
    try:
        print('Response JSON:', resp.json())
    except Exception:
        print('Response text:', resp.text)

if __name__ == '__main__':
    main()
