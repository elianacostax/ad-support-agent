
from app.db.database import SessionLocal
from app.models.approval_request import ApprovalRequest
from app.services.approval_service import ApprovalService

db = SessionLocal()
try:
    approval = db.get(ApprovalRequest, 26)
    print(f"Approval status: {approval.status}")
    print(f"Approval action: {approval.action}")
    
    # Create service and try to execute the approved request
    service = ApprovalService(db)
    try:
        result = service.execute_approved_request(request_id=26)
        print(f"Execution result: {result.status} - {result.result}")
    except ValueError as e:
        print(f"ValueError: {e}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
finally:
    db.close()

print("\n=== Test Complete ===")