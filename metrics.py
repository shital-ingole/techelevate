# metrics.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import SessionLocal
from models import Employee

router = APIRouter(prefix="/metrics")

async def get_db():
    async with SessionLocal() as session:
        yield session

@router.get("/total_employees")
async def total_employees(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Employee))
    employees = result.scalars().all()
    return {"total_employees": len(employees)}
