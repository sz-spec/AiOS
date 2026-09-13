"""
Payment Setup Routes
====================
API endpoints for payment integration setup.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service
from services.payment_builder import generate_payment_code

router = APIRouter(prefix="/api/v1/payments", tags=["Payments"])


class ProductDef(BaseModel):
    id: str
    name: str
    price: str
    description: str = ""
    recurring: bool = False
    interval: str = "month"


class PaymentSetupRequest(BaseModel):
    project_id: str
    products: List[ProductDef]
    stripe_connected: bool = False


class PaymentSetupResponse(BaseModel):
    files_generated: int


@router.post("/setup", response_model=PaymentSetupResponse)
async def setup_payments(
    req: PaymentSetupRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Generate payment integration code for a project."""
    service = get_project_service()
    project = service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    products_dicts = [p.model_dump() for p in req.products]
    files = generate_payment_code(products_dicts)
    service.update_files(req.project_id, files)

    return PaymentSetupResponse(files_generated=len(files))
