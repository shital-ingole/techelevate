from fastapi import (
    FastAPI, Depends, Form, File, UploadFile, HTTPException, 
    status, Request, APIRouter, WebSocket, WebSocketDisconnect
)
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
from typing import List, Optional, Dict, Union
from datetime import date, datetime, timedelta
from enum import Enum
import io
import base64
import uuid
import os
import json
import re
import pandas as pd
import csv
import shutil
from io import StringIO
import pandas as pd

# Redis setup
import redis

# Initialize Redis client (adjust host/port as needed)
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# Import local modules
import models, schemas, utils, auth, database
from database import get_db, engine
from auth import get_current_user, require_role
from email_service import email_service
from schemas import (    
    ReportResponse, 
    ReportDetailResponse, 
    CommentCreate,
    BulkToggleResponse,
    BulkDeleteResponse, 
    MCQQuestionBulkToggle,
    MCQQuestionBulkDelete,
    LevelDateBase,
    CertificateResponse,
)

#++++++++++++
#++++++++++++
# Security service initialization
# Security service initialization - WITHOUT heartbeat and noise monitoring
# Update your SecurityService class
class SecurityService:
    def __init__(self):
        self.active_sessions = {}
        self.violation_threshold = 3
        self.camera_warnings_given = {}  # Track camera warnings per exam
        
    async def start_proctoring_session(self, exam_attempt_id: int, user_id: int):
        """Start proctoring session for exam - with camera warning tracking"""
        session_data = {
            'exam_attempt_id': exam_attempt_id,
            'user_id': user_id,
            'start_time': datetime.utcnow(),
            'violations': [],
            'warnings': 0,
            'screenshots': [],
            'is_active': True,
            'camera_warning_given': False  # Track if camera warning was already given
        }
        self.active_sessions[exam_attempt_id] = session_data
        self.camera_warnings_given[exam_attempt_id] = False
        return session_data
    
    async def record_violation(self, exam_attempt_id: int, violation_type: str, details: dict = None):
        """Record security violation with special handling for camera issues"""
        if exam_attempt_id not in self.active_sessions:
            return False
           
        session = self.active_sessions[exam_attempt_id]
        
        # Special handling for camera not accessible - give warning first
        if violation_type == "camera_not_accessible":
            if not self.camera_warnings_given.get(exam_attempt_id, False):
                # First camera warning - don't count as violation, just warn
                self.camera_warnings_given[exam_attempt_id] = True
                print(f"First camera warning for exam {exam_attempt_id} - allowing exam to continue")
                return False  # Don't auto-submit, just warn
        
        violation = {
            'type': violation_type,
            'timestamp': datetime.utcnow(),
            'details': details or {}
        }
        
        session['violations'].append(violation)
        
        # Only increment warning count for non-camera violations or subsequent camera violations
        if not (violation_type == "camera_not_accessible" and self.camera_warnings_given[exam_attempt_id]):
            session['warnings'] += 1
        
        print(f"Violation recorded: {violation_type}. Warnings: {session['warnings']}/{self.violation_threshold}")
        
        # Check if should auto-submit (skip for first camera warning)
        if session['warnings'] >= self.violation_threshold:
            print(f"Auto-submitting exam {exam_attempt_id} due to {session['warnings']} violations")
            await self.auto_submit_exam(exam_attempt_id, "max_violations_reached")
            return True
            
        return False
    
    # ... rest of your SecurityService methods remain the same
    
    async def auto_submit_exam(self, exam_attempt_id: int, reason: str):
        """Automatically submit exam due to violations - FIXED VERSION"""
        try:
            db = next(get_db())
            
            exam_attempt = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.id == exam_attempt_id
            ).first()
            
            if exam_attempt and exam_attempt.status == 'in_progress':
                print(f"Auto-submitting exam {exam_attempt_id} due to {reason}")
                
                # Update exam status to auto_submitted
                exam_attempt.status = 'auto_submitted'
                exam_attempt.completed_at = datetime.utcnow()
                exam_attempt.percentage_score = 0
                exam_attempt.correct_answers = 0
                exam_attempt.total_marks = 0
                exam_attempt.questions_attempted = 0
                
                # Update assignment status to failed
                assignment = exam_attempt.assignment
                if assignment:
                    assignment.status = models.TrainingStatus.failed
                    assignment.updated_at = datetime.utcnow()
                
                # Create proctoring record if it doesn't exist
                proctoring = db.query(models.ExamProctoring).filter(
                    models.ExamProctoring.exam_attempt_id == exam_attempt_id
                ).first()
                
                if not proctoring:
                    proctoring = models.ExamProctoring(
                        exam_attempt_id=exam_attempt_id,
                        face_detected=False,
                        movement_count=0,
                        warning_count=self.violation_threshold,
                        screenshots_taken=[],
                        violations_detected=self.active_sessions.get(exam_attempt_id, {}).get('violations', []),
                        created_at=datetime.utcnow()
                    )
                    db.add(proctoring)
                else:
                    proctoring.warning_count = self.violation_threshold
                    proctoring.violations_detected = self.active_sessions.get(exam_attempt_id, {}).get('violations', [])
                
                db.commit()
                
                print(f"Exam {exam_attempt_id} auto-submitted successfully due to {reason}")
                
                # Remove from active sessions
                if exam_attempt_id in self.active_sessions:
                    del self.active_sessions[exam_attempt_id]
                    
            else:
                print(f"Exam {exam_attempt_id} not found or already submitted: {exam_attempt.status if exam_attempt else 'not found'}")
                
        except Exception as e:
            print(f"Error auto-submitting exam: {str(e)}")
            # Don't re-raise to prevent breaking the violation recording
            try:
                db.rollback()
            except:
                pass
    
    async def send_violation_notification(self, exam_attempt_id: int, reason: str):
        """Send violation notification"""
        try:
            db = next(get_db())
            
            exam_attempt = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.id == exam_attempt_id
            ).first()
            
            if exam_attempt:
                employee = exam_attempt.assignment.user
                training = exam_attempt.assignment.training
                
                subject = f"Exam Auto-Submitted Due to Violations - {employee.full_name}"
                content = f"""
                <html>
                <body>
                    <h2>Exam Auto-Submitted Due to Security Violations</h2>
                    <p><strong>Employee:</strong> {employee.full_name}</p>
                    <p><strong>Training:</strong> {training.title}</p>
                    <p><strong>Level:</strong> {exam_attempt.training_level.level_name}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    <p><strong>Violations Count:</strong> {self.violation_threshold}</p>
                    <p>The exam has been automatically submitted and marked as failed due to security violations.</p>
                </body>
                </html>
                """
                
                # Send to admins
                admin_emails = get_admin_emails(db)
                for email in admin_emails:
                    try:
                        email_service.send_email(email, subject, content)
                    except Exception as e:
                        print(f"Failed to send violation email: {str(e)}")
                        
        except Exception as e:
            print(f"Error sending violation notification: {str(e)}")

# Initialize security service
security_service = SecurityService()

def get_admin_emails(db: Session):
    """Get all admin email addresses"""
    try:
        admins = db.query(models.User).filter(
            models.User.role == models.UserRole.admin,
            models.User.is_active == True
        ).all()
        return [admin.email for admin in admins if admin.email]
    except Exception as e:
        print(f"Error getting admin emails: {str(e)}")
        return []  # Return empty list as fallback

#+++++++++++
#++++++++++++
# Create database tables
models.Base.metadata.create_all(bind=engine)
router = APIRouter(prefix="/reports", tags=["Reports"])
app = FastAPI(
    title="Employee Training Tracker API",
    description="A comprehensive training management system for AASPL organization",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url=None,
    redoc_url=None,
    root_path="/api"
)

from fastapi.openapi.docs import get_swagger_ui_html

@app.get("/docs", include_in_schema=False)
async def get_documentation(
    request: Request,
    db: Session = Depends(get_db)
):
    token = request.cookies.get("access_token")
    if not token:
        # Relative redirect to login page (sibling path)
        return RedirectResponse(url="./docs/login")
    
    try:
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
        token_data = auth.verify_token(token, credentials_exception)
        
        # Verify user exists and is admin
        user = db.query(models.User).filter(models.User.username == token_data["username"]).first()
        if not user or user.role != models.UserRole.admin:
            return RedirectResponse(url="./docs/login?error=Unauthorized")
            
        return get_swagger_ui_html(openapi_url=f"{app.root_path}{app.openapi_url}", title="Swagger UI")
    except HTTPException:
        return RedirectResponse(url="./docs/login")

@app.get("/docs/login", include_in_schema=False)
async def login_for_docs_page(request: Request, error: Optional[str] = None):
    error_html = f'<p style="color: red;">{error}</p>' if error else ''
    # Post back to same URL
    login_url = "login" 
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TechElevate API Docs - Login</title>
        <style>
            body {{ font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background-color: #f0f2f5; }}
            .login-box {{ background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px; }}
            input {{ width: 100%; padding: 8px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
            button {{ width: 100%; padding: 10px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }}
            button:hover {{ background-color: #0056b3; }}
            h2 {{ text-align: center; color: #333; margin-top: 0; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>Admin Login</h2>
            {error_html}
            <form action="{login_url}" method="post" autocomplete="off">
                <label>Username</label>
                <input type="text" name="username" required autocomplete="current-password"> <!-- trick to disable chrome autofill sometimes -->
                <label>Password</label>
                <input type="password" name="password" required autocomplete="new-password">
                <button type="submit">Access Documentation</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/docs/login", include_in_schema=False)
async def login_for_docs_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Authenticate User
    user = db.query(models.User).filter(
        or_(models.User.username == username, models.User.email == username)
    ).first()
    
    if not user or not utils.verify_password(password, user.password_hash):
        return RedirectResponse(
            url="login?error=Invalid Credentials", 
            status_code=status.HTTP_302_FOUND
        )
        
    if user.role != models.UserRole.admin:
        return RedirectResponse(
            url="login?error=Admins Only", 
            status_code=status.HTTP_302_FOUND
        )
        
    # Create Token
    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role.value}
    )
    
    # Redirect up one level to /docs (or wherever the user came from)
    response = RedirectResponse(url="../docs", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=18000, # 5 hours
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https"  # Secure in production
    )
    return response

@app.get("/docs/logout", include_in_schema=False)
async def logout_from_docs(request: Request):
    # Redirect to login page
    response = RedirectResponse(
        url="login?error=Logged Out"
    )
    response.delete_cookie("access_token", path="/")
    return response

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add ProxyHeadersMiddleware to trust the proxy
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

def can_mark_as_completed(assignment: models.Assignment, db: Session) -> tuple[bool, str]:
    """
    Check if an assignment can be marked as completed
    Returns (is_eligible, reason)
    """
    # Get all levels for this training
    training_levels = (
        db.query(models.TrainingLevel)
        .filter(models.TrainingLevel.training_id == assignment.training_id)
        .order_by(models.TrainingLevel.level_order.asc())
        .all()
    )
    
    if not training_levels:
        return False, "No training levels found"
    
    # Check if employee is on the final level
    final_level = training_levels[-1]
    if assignment.current_level_id != final_level.id:
        return False, f"Must complete all {len(training_levels)} levels first"
    
    # Check if employee has passed the final level
    final_level_evaluation = (
        db.query(models.Evaluation)
        .filter(
            models.Evaluation.assignment_id == assignment.id,
            models.Evaluation.training_level_id == final_level.id,
        )
        .order_by(models.Evaluation.evaluation_date.desc())
        .first()
    )
    
    if not final_level_evaluation or final_level_evaluation.status != models.EvaluationStatus.passed:
        return False, f"Final level '{final_level.level_name}' not passed. Required: {final_level.pass_percentage}%"
    
    return True, "Eligible for completion"
# ---------------- Users Endpoints ----------------
@app.post("/users/register", response_model=schemas.UserOut)
def register_user(
    user: schemas.UserCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    try:
        # Check if username already exists
        existing_user = (
            db.query(models.User)
            .filter(
                or_(
                    models.User.username == user.username,
                    models.User.email == user.email,
                )
            )
            .first()
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username or email already registered",
            )

        # Generate unique user code
        existing_codes = [u.user_code for u in db.query(models.User).all()]

        if user.user_code in existing_codes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User code '{user.user_code}' already exists"
            )
        else:
            user_code = user.user_code

        # Create new user
        user_data = user.model_dump()
        password = user_data.pop("password")
        hashed_password = utils.hash_password(password)

        user_data.pop("user_code", None)

        new_user = models.User(
            user_code=user_code, **user_data, password_hash=hashed_password
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating user: {str(e)}",
        )


@app.post("/users/login", response_model=schemas.Token)
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(), 
    request: Request = None,
    db: Session = Depends(get_db)
):
    try:
        username_or_email = form_data.username

        if "@" in username_or_email:
            user = (
                db.query(models.User)
                .filter(models.User.email == username_or_email)
                .first()
            )
        else:
            user = (
                db.query(models.User)
                .filter(models.User.username == username_or_email)
                .first()
            )

        if (
            not user
            or not user.is_active
            or not utils.verify_password(form_data.password, user.password_hash)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )

        access_token = auth.create_access_token(
            data={"sub": user.username, "role": user.role.value}
        )

        # Log the login activity
        try:
            ip_address = request.client.host if request else None
            user_agent = request.headers.get("user-agent") if request else None
            
            login_log = models.UserLoginLog(
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            db.add(login_log)
            db.commit()
            
            # Include log ID in response for potential logout tracking
            response_data = {"access_token": access_token, "token_type": "bearer", "login_log_id": login_log.id}
            
        except Exception as log_error:
            # Don't fail the login if logging fails
            print(f"Failed to log login: {str(log_error)}")
            response_data = {"access_token": access_token, "token_type": "bearer"}

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login error: {str(e)}",
        )


@app.get("/users/me", response_model=schemas.UserOut)
def get_current_user_profile(current_user: models.User = Depends(get_current_user)):
    return current_user


@app.get("/users", response_model=List[schemas.UserOut])
def list_users(
    skip: int = 0,
   # limit: int = 100,#+++++++++++
    department: Optional[str] = None,
    role: Optional[schemas.UserRole] = None,
    is_active: Optional[bool] = None,  # Add this parameter
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    # Create a cache key based on query params
    cache_key = f"users:skip={skip}:department={department}:role={role}:is_active={is_active}"
    try:
        cached_users = redis_client.get(cache_key)
        if cached_users:
            return json.loads(cached_users)
    except Exception as e:
        print(f"[Redis] Cache get error: {e}")

    query = db.query(models.User)
    if department:
        query = query.filter(models.User.department == department)
    if role:
        query = query.filter(models.User.role == role)
    if is_active is not None:
        query = query.filter(models.User.is_active == is_active)

    users = query.offset(skip).all()
    # Serialize users for caching (using schema)
    users_data = [schemas.UserOut.from_orm(user).dict() for user in users]
    try:
        redis_client.setex(cache_key, 60, json.dumps(users_data))  # Cache for 60 seconds
    except Exception as e:
        print(f"[Redis] Cache set error: {e}")
    return users_data

@app.get("/users/{user_id}", response_model=schemas.UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    cache_key = f"user:{user_id}"
    try:
        cached_user = redis_client.get(cache_key)
        if cached_user:
            return json.loads(cached_user)
    except Exception as e:
        print(f"[Redis] Cache get error: {e}")

    user = (
        db.query(models.User)
        .filter(models.User.id == user_id, models.User.is_active == True)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if current_user.role != models.UserRole.admin and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    try:
        redis_client.setex(cache_key, 60, json.dumps(schemas.UserOut.from_orm(user).dict()))
    except Exception as e:
        print(f"[Redis] Cache set error: {e}")
    return user


@app.put("/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    user_update: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != models.UserRole.admin and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    db_user = (
        db.query(models.User)
        .filter(models.User.id == user_id, models.User.is_active == True)
        .first()
    )

    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    update_data = user_update.dict(exclude_unset=True)

    if "password" in update_data:
        db_user.password_hash = utils.hash_password(update_data.pop("password"))

    # Validate user_code uniqueness if being updated
    if "user_code" in update_data:
        new_user_code = update_data["user_code"]
        if not new_user_code.startswith("AASPL-"):
            new_user_code = f"AASPL-{new_user_code}"
            update_data["user_code"] = new_user_code
        existing_user = db.query(models.User).filter(
            models.User.user_code == new_user_code, models.User.id != user_id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User code already exists"
            )

    # Validate username uniqueness if being updated
    if "username" in update_data:
        existing_username = db.query(models.User).filter(
            models.User.username == update_data["username"],
            models.User.id != user_id
        ).first()
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists"
            )

    for key, value in update_data.items():
        setattr(db_user, key, value)

    db_user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_user)

    return db_user


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    try:
        # 1. Delete MCQ answers and exam attempts for user's assignments
        user_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == user_id
        ).all()
        
        assignment_ids = [assignment.id for assignment in user_assignments]
        
        if assignment_ids:
            # Delete MCQ answers for user's exam attempts
            exam_attempts = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.assignment_id.in_(assignment_ids)
            ).all()
            
            for attempt in exam_attempts:
                # Delete MCQ answers first
                db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.exam_attempt_id == attempt.id
                ).delete()
            # Delete exam attempts
            db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.assignment_id.in_(assignment_ids)
            ).delete()

        # 2. Delete evaluations for user's assignments
        if assignment_ids:
            db.query(models.Evaluation).filter(
                models.Evaluation.assignment_id.in_(assignment_ids)
            ).delete()

        # 3. Delete training progress records
        db.query(models.TrainingProgress).filter(
            models.TrainingProgress.user_id == user_id
        ).delete()

        # 4. Delete assignments
        if assignment_ids:
            db.query(models.Assignment).filter(
                models.Assignment.id.in_(assignment_ids)
            ).delete()

        # 5. Delete MCQ questions created by this user
        # First get the training level IDs for questions created by this user
        user_mcq_questions = db.query(models.MCQQuestion).filter(
            models.MCQQuestion.created_by == user_id
        ).all()
        
        # Delete MCQ answers that reference these questions
        question_ids = [question.id for question in user_mcq_questions]
        if question_ids:
            # Find exam attempts that have answers to these questions
            exam_attempts_with_user_questions = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.id.in_(
                    db.query(models.MCQAnswer.exam_attempt_id).filter(
                        models.MCQAnswer.question_id.in_(question_ids)
                    )
                )
            ).all()
            
            # Delete answers for these questions
            db.query(models.MCQAnswer).filter(
                models.MCQAnswer.question_id.in_(question_ids)
            ).delete()
            
            # Update or delete exam attempts that referenced these questions
            for attempt in exam_attempts_with_user_questions:
                # Recalculate attempt stats or delete if no questions left
                remaining_answers = db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.exam_attempt_id == attempt.id
                ).count()
                if remaining_answers == 0:
                    db.delete(attempt)
        
        # Now delete the MCQ questions
        db.query(models.MCQQuestion).filter(
            models.MCQQuestion.created_by == user_id
        ).delete()

        # 6. Handle trainings created by this user
        user_trainings = db.query(models.Training).filter(
            models.Training.created_by == user_id
        ).all()
        
        for training in user_trainings:
            # Get all training levels for this training
            training_levels = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.training_id == training.id
            ).all()
            
            training_level_ids = [level.id for level in training_levels]
            
            # Delete MCQ questions for these training levels FIRST
            if training_level_ids:
                # Get MCQ questions for these levels
                mcq_questions_for_levels = db.query(models.MCQQuestion).filter(
                    models.MCQQuestion.training_level_id.in_(training_level_ids)
                ).all()
                
                question_ids_for_levels = [q.id for q in mcq_questions_for_levels]
                
                # Delete MCQ answers that reference these questions
                if question_ids_for_levels:
                    db.query(models.MCQAnswer).filter(
                        models.MCQAnswer.question_id.in_(question_ids_for_levels)
                    ).delete()
                
                # Delete the MCQ questions
                db.query(models.MCQQuestion).filter(
                    models.MCQQuestion.training_level_id.in_(training_level_ids)
                ).delete()
            
            # Get assignments for this training
            training_assignments = db.query(models.Assignment).filter(
                models.Assignment.training_id == training.id
            ).all()
            
            training_assignment_ids = [assignment.id for assignment in training_assignments]
            
            # Delete evaluations for these assignments
            if training_assignment_ids:
                db.query(models.Evaluation).filter(
                    models.Evaluation.assignment_id.in_(training_assignment_ids)
                ).delete()
                
                # Delete MCQ exam attempts for these assignments
                db.query(models.MCQExamAttempt).filter(
                    models.MCQExamAttempt.assignment_id.in_(training_assignment_ids)
                ).delete()
            
            # Delete assignments for this training
            if training_assignment_ids:
                db.query(models.Assignment).filter(
                    models.Assignment.id.in_(training_assignment_ids)
                ).delete()
            
            # Delete training levels for this training
            if training_level_ids:
                db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.id.in_(training_level_ids)
                ).delete()
            
            # Finally delete the training
            db.delete(training)

        # 7. Handle training levels created by this user (that aren't part of user's trainings)
        user_training_levels = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.created_by == user_id
        ).all()
        
        for level in user_training_levels:
            # Delete MCQ questions for this level first
            mcq_questions_for_level = db.query(models.MCQQuestion).filter(
                models.MCQQuestion.training_level_id == level.id
            ).all()
            
            question_ids_for_level = [q.id for q in mcq_questions_for_level]
            
            # Delete MCQ answers that reference these questions
            if question_ids_for_level:
                db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.question_id.in_(question_ids_for_level)
                ).delete()
            
            # Delete the MCQ questions
            db.query(models.MCQQuestion).filter(
                models.MCQQuestion.training_level_id == level.id
            ).delete()
            
            # Update assignments that reference this level
            assignments_with_level = db.query(models.Assignment).filter(
                models.Assignment.current_level_id == level.id
            ).all()
            
            for assignment in assignments_with_level:
                # Find another level in the same training to assign, or set to None
                alternative_level = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.training_id == assignment.training_id,
                    models.TrainingLevel.id != level.id
                ).first()
                
                if alternative_level:
                    assignment.current_level_id = alternative_level.id
                else:
                    # If no alternative level, delete the assignment
                    db.delete(assignment)
            
            # Delete the training level
            db.delete(level)

        # 8. Update trainings where this user is the updater
        db.query(models.Training).filter(
            models.Training.updated_by == user_id
        ).update({models.Training.updated_by: None})
        
        # 9. Update assignments where this user is the updater
        db.query(models.Assignment).filter(
            models.Assignment.updated_by == user_id
        ).update({models.Assignment.updated_by: None})

        # 10. Finally delete the user
        db.delete(user)
        db.commit()

        return None

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting user and related data: {str(e)}",
        )
@app.put("/users/{user_id}/toggle-active", response_model=schemas.UserOut)
def toggle_user_active_status(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    user.is_active = not user.is_active
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return user

# ---------------- Training Endpoints ----------------

@app.post("/trainings", response_model=schemas.TrainingOut)
async def create_training(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    has_levels: str = Form("false"),
    levels: Optional[str] = Form(None),
    training_details: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    try:
        # Check for duplicate training
        existing_training = (
            db.query(models.Training)
            .filter(func.lower(models.Training.title) == func.lower(title))
            .first()
        )
        if existing_training:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Training with this title already exists",
            )

        # Convert has_levels to boolean
        has_levels_bool = has_levels.lower() == "true"

        # Create main training record
        new_training = models.Training(
            title=title,
            description=description,
            category=category,
            has_levels=has_levels_bool,
            created_by=current_user.id,
        )
        db.add(new_training)
        db.flush()

        if has_levels_bool:
            # Training with levels - existing logic
            if not levels:
                raise HTTPException(status_code=400, detail="Levels data is required for training with levels")
            
            levels_data = json.loads(levels)
            if not levels_data or len(levels_data) == 0:
                raise HTTPException(status_code=400, detail="Training with levels must have at least one level")

            level_orders = [lvl["level_order"] for lvl in levels_data]
            if len(level_orders) != len(set(level_orders)):
                raise HTTPException(status_code=400, detail="Level orders must be unique")

            # Save each level
            for lvl in levels_data:
                new_level = models.TrainingLevel(
                    training_id=new_training.id,
                    created_by=current_user.id,
                    **lvl,
                )
                db.add(new_level)
        else:
            # Training without levels - create a single level with training details
            training_details_data = {}
            if training_details:
                training_details_data = json.loads(training_details)
            
            # Create a single level with the training details - use "Main" as level name
            new_level = models.TrainingLevel(
                training_id=new_training.id,
                level_name="Complete Training",  # Changed from "Main" to indicate it's the full training
                level_order=1,
                description=description,  # Use training description
                duration_hours=training_details_data.get("duration_hours"),
                duration_minutes=training_details_data.get("duration_minutes"),  # ADDED THIS LINE
                prerequisites=training_details_data.get("prerequisites"),
                learning_objectives=training_details_data.get("learning_objectives"),
                learning_plan_links=training_details_data.get("learning_plan_links"),
                learning_materials=training_details_data.get("learning_materials"),
                pass_percentage=training_details_data.get("pass_percentage", 60),
                max_attempts=training_details_data.get("max_attempts", 3),
                created_by=current_user.id,
            )
            db.add(new_level)

        db.commit()
        db.refresh(new_training)

        # Handle file upload (existing code)
        if file:
            allowed_exts = [".pdf", ".doc", ".docx", ".xlsx", ".xls", ".txt", ".csv"]
            orig_filename = file.filename or ""
            _, ext = os.path.splitext(orig_filename)
            ext = ext.lower()

            if ext not in allowed_exts:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid file type. Allowed: {', '.join(allowed_exts)}",
                )

            upload_dir = os.path.join(os.getcwd(), "uploads")
            os.makedirs(upload_dir, exist_ok=True)

            file_path = os.path.join(upload_dir, f"training_{new_training.id}{ext}")
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            new_training.learning_materials = file_path
            db.commit()
            db.refresh(new_training)

        return new_training

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating training: {str(e)}")


@app.post("/assignments/{assignment_id}/level-dates")
def update_level_dates(
    assignment_id: int,
    level_dates: Dict[str, Dict[str, str]],  # {level_id: {start_date, due_date}}
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Update level dates for an assignment"""
    try:
        assignment = db.query(models.Assignment).filter(models.Assignment.id == assignment_id).first()
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Delete existing level dates for this assignment
        db.query(models.AssignmentLevelDate).filter(
            models.AssignmentLevelDate.assignment_id == assignment_id
        ).delete()

        # Create new level dates
        for level_id_str, dates in level_dates.items():
            try:
                level_id = int(level_id_str)
                # Verify the level exists in this training
                level_exists = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.id == level_id,
                    models.TrainingLevel.training_id == assignment.training_id
                ).first()
                
                if level_exists:
                    start_date = datetime.fromisoformat(dates.get('start_date').replace('Z', '+00:00')) if dates.get('start_date') else None
                    due_date = datetime.fromisoformat(dates.get('due_date').replace('Z', '+00:00')) if dates.get('due_date') else None
                    
                    level_date = models.AssignmentLevelDate(
                        assignment_id=assignment_id,
                        level_id=level_id,
                        start_date=start_date,
                        due_date=due_date
                    )
                    db.add(level_date)
            except (ValueError, TypeError) as e:
                print(f"Error processing level date for level {level_id_str}: {str(e)}")
                continue

        db.commit()
        return {"message": "Level dates updated successfully"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating level dates: {str(e)}")
    
@app.get("/assignments/{assignment_id}/level-dates")
def get_level_dates(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Get level dates for an assignment"""
    try:
        level_dates = db.query(models.AssignmentLevelDate).filter(
            models.AssignmentLevelDate.assignment_id == assignment_id
        ).all()

        result = {}
        for level_date in level_dates:
            result[str(level_date.level_id)] = {
                "start_date": level_date.start_date.isoformat() if level_date.start_date else None,
                "due_date": level_date.due_date.isoformat() if level_date.due_date else None
            }

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching level dates: {str(e)}")

@app.get("/trainings", response_model=List[schemas.TrainingOut])
def list_trainings(
    skip: int = 0,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        query = db.query(models.Training)

        if category:
            query = query.filter(models.Training.category == category)

        trainings = query.offset(skip).all()

        # Add creator/updater names and levels with proper error handling
        result = []
        for training in trainings:
            # Safely get creator name
            creator_name = "Unknown"
            if training.creator:
                creator_name = f"{training.creator.first_name} {training.creator.last_name}"
            
            # Safely get updater name
            updater_name = None
            if training.updater:
                updater_name = f"{training.updater.first_name} {training.updater.last_name}"

            training_dict = {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
                "has_levels": training.has_levels,
                "created_by": training.created_by,
                "updated_by": training.updated_by,
                "created_at": training.created_at,
                "updated_at": training.updated_at,
                "levels": [],
                "creator_name": creator_name,
                "updater_name": updater_name,
            }

            # Add levels with safe attribute access
            for level in training.levels:
                level_data = {
                    "id": level.id,
                    "level_name": level.level_name,
                    "level_order": level.level_order,
                    "description": level.description,
                    "duration_hours": level.duration_hours,
                    "prerequisites": level.prerequisites,
                    "learning_objectives": level.learning_objectives,
                    "learning_plan_links": level.learning_plan_links,
                    "learning_materials": level.learning_materials,
                    "pass_percentage": level.pass_percentage,
                    "max_attempts": level.max_attempts,
                    "exam_questions_count": level.exam_questions_count,
                    "exam_duration_minutes": level.exam_duration_minutes,
                    "training_id": level.training_id,
                    "created_at": level.created_at,
                }
                training_dict["levels"].append(level_data)

            result.append(training_dict)

        return result

    except Exception as e:
        print(f"Error in list_trainings: {str(e)}")  # Debug logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching trainings: {str(e)}"
        )


@app.get("/trainings/{training_id}", response_model=schemas.TrainingOut)
def get_training(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        training = (
            db.query(models.Training).filter(models.Training.id == training_id).first()
        )
        if not training:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Training not found"
            )

        # Safely get creator name
        creator_name = "Unknown"
        if training.creator:
            creator_name = f"{training.creator.first_name} {training.creator.last_name}"
        
        # Safely get updater name
        updater_name = None
        if training.updater:
            updater_name = f"{training.updater.first_name} {training.updater.last_name}"

        training_dict = {
            "id": training.id,
            "title": training.title,
            "description": training.description,
            "category": training.category,
            "has_levels": training.has_levels,
            "created_by": training.created_by,
            "updated_by": training.updated_by,
            "created_at": training.created_at,
            "updated_at": training.updated_at,
            "levels": [],
            "creator_name": creator_name,
            "updater_name": updater_name,
        }

        # Add levels with safe attribute access
        for level in training.levels:
            level_data = {
                "id": level.id,
                "level_name": level.level_name,
                "level_order": level.level_order,
                "description": level.description,
                "duration_hours": level.duration_hours,
                "prerequisites": level.prerequisites,
                "learning_objectives": level.learning_objectives,
                "learning_plan_links": level.learning_plan_links,
                "learning_materials": level.learning_materials,
                "pass_percentage": level.pass_percentage,
                "max_attempts": level.max_attempts,
                "exam_questions_count": level.exam_questions_count,
                "exam_duration_minutes": level.exam_duration_minutes,
                "training_id": level.training_id,
                "created_at": level.created_at,
            }
            training_dict["levels"].append(level_data)

        return training_dict

    except Exception as e:
        print(f"Error in get_training: {str(e)}")  # Debug logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching training: {str(e)}"
        )

@app.put("/trainings/{training_id}", response_model=schemas.TrainingOut)
def update_training(
    training_id: int,
    training_update: schemas.TrainingUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    db_training = (
        db.query(models.Training).filter(models.Training.id == training_id).first()
    )
    if not db_training:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training not found"
        )

    update_data = training_update.dict(exclude_unset=True)

    for key, value in update_data.items():
        setattr(db_training, key, value)

    db_training.updated_by = current_user.id
    db_training.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(db_training)

    return db_training


@app.delete("/trainings/{training_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_training(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Delete training and all related data including assignments, exams, and scores"""
    try:
        # Find the training
        training = db.query(models.Training).filter(models.Training.id == training_id).first()
        
        if not training:
            raise HTTPException(status_code=404, detail="Training not found")

        # Get all assignments for this training
        assignments = db.query(models.Assignment).filter(models.Assignment.training_id == training_id).all()
        assignment_ids = [assignment.id for assignment in assignments]

        # *** DELETE RELATED DATA IN PROPER ORDER ***

        # 1. Delete MCQ answers and exam attempts for assignments
        if assignment_ids:
            # Get all exam attempts for these assignments
            exam_attempts = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.assignment_id.in_(assignment_ids)
            ).all()
            
            exam_attempt_ids = [attempt.id for attempt in exam_attempts]
            
            # Delete MCQ answers first
            if exam_attempt_ids:
                db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.exam_attempt_id.in_(exam_attempt_ids)
                ).delete(synchronize_session=False)
            
            # Delete exam attempts
            db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.assignment_id.in_(assignment_ids)
            ).delete(synchronize_session=False)

        # 2. Delete evaluations for assignments
        if assignment_ids:
            db.query(models.Evaluation).filter(
                models.Evaluation.assignment_id.in_(assignment_ids)
            ).delete(synchronize_session=False)

        # 3. Delete training progress records
        db.query(models.TrainingProgress).filter(
            models.TrainingProgress.training_id == training_id
        ).delete(synchronize_session=False)

        # 4. Delete assignments
        if assignment_ids:
            db.query(models.Assignment).filter(
                models.Assignment.id.in_(assignment_ids)
            ).delete(synchronize_session=False)

        # 5. Get all training level IDs for this training
        training_levels = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == training_id
        ).all()
        
        training_level_ids = [level.id for level in training_levels]

        # 6. Delete MCQ questions for these training levels
        if training_level_ids:
            # Get MCQ questions for these levels
            mcq_questions = db.query(models.MCQQuestion).filter(
                models.MCQQuestion.training_level_id.in_(training_level_ids)
            ).all()
            
            question_ids = [question.id for question in mcq_questions]
            
            # Delete any remaining MCQ answers that reference these questions
            if question_ids:
                db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.question_id.in_(question_ids)
                ).delete(synchronize_session=False)
            
            # Delete the MCQ questions
            db.query(models.MCQQuestion).filter(
                models.MCQQuestion.training_level_id.in_(training_level_ids)
            ).delete(synchronize_session=False)

        # 7. Delete training levels
        if training_level_ids:
            db.query(models.TrainingLevel).filter(
                models.TrainingLevel.id.in_(training_level_ids)
            ).delete(synchronize_session=False)

        # 8. Finally delete the training
        db.delete(training)
        db.commit()

        return None

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error deleting training and related data: {str(e)}"
        )
@app.get("/assignments/{assignment_id}/exam-attempts")
def get_assignment_exam_attempts(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get user's exam attempts for current level with attempt limits"""
    try:
        assignment = db.query(models.Assignment).filter(models.Assignment.id == assignment_id).first()
        
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Verify ownership
        if current_user.role != models.UserRole.admin and assignment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized for this assignment")

        # Get current training level
        current_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == assignment.current_level_id
        ).first()

        if not current_level:
            raise HTTPException(status_code=404, detail="Current level not found")

        # Get exam attempts for current level
        exam_attempts = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == assignment_id,
            models.MCQExamAttempt.training_level_id == assignment.current_level_id
        ).order_by(models.MCQExamAttempt.started_at.desc()).all()

        # Calculate remaining attempts
        failed_attempts = len([attempt for attempt in exam_attempts if attempt.status == 'failed'])
        remaining_attempts = max(0, current_level.max_attempts - failed_attempts)
        
        # Check if user has passed already
        passed_attempt = any(attempt.status == 'passed' for attempt in exam_attempts)

        return {
            "assignment_id": assignment_id,
            "current_level": current_level.level_name,
            "max_attempts": current_level.max_attempts,
            "used_attempts": len(exam_attempts),
            "failed_attempts": failed_attempts,
            "remaining_attempts": remaining_attempts,
            "has_passed": passed_attempt,
            "passing_percentage": current_level.pass_percentage,
            "attempts": [
                {
                    "id": attempt.id,
                    "percentage_score": attempt.percentage_score,
                    "status": attempt.status,
                    "started_at": attempt.started_at,
                    "completed_at": attempt.completed_at
                }
                for attempt in exam_attempts
            ]
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching exam attempts: {str(e)}"
        )

# ---------------- Training Level Endpoints ----------------
@app.put("/training-levels/{level_id}")
def update_training_level_with_exam_settings(
    level_id: int,
    level_update: schemas.TrainingLevelUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        db_level = db.query(models.TrainingLevel).filter(models.TrainingLevel.id == level_id).first()
        if not db_level:
            raise HTTPException(status_code=404, detail="Training level not found")

        # Handle exam settings
        if level_update.exam_questions_count is not None:
            db_level.exam_questions_count = level_update.exam_questions_count
        if level_update.exam_duration_minutes is not None:
            db_level.exam_duration_minutes = level_update.exam_duration_minutes

        # Handle other level updates
        if level_update.level_name is not None:
            db_level.level_name = level_update.level_name
        if level_update.description is not None:
            db_level.description = level_update.description
        if level_update.pass_percentage is not None:
            db_level.pass_percentage = level_update.pass_percentage
        if level_update.max_attempts is not None:
            db_level.max_attempts = level_update.max_attempts

        db.commit()
        db.refresh(db_level)

        return {
            "message": "Training level updated successfully",
            "level": {
                "id": db_level.id,
                "level_name": db_level.level_name,
                "exam_questions_count": db_level.exam_questions_count,
                "exam_duration_minutes": db_level.exam_duration_minutes,
                "pass_percentage": db_level.pass_percentage,
                "max_attempts": db_level.max_attempts
            }
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating training level: {str(e)}")
@app.get("/training-levels/{level_id}", response_model=schemas.TrainingLevelOut)
def get_training_level(
    level_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    level = (
        db.query(models.TrainingLevel)
        .filter(models.TrainingLevel.id == level_id)
        .first()
    )
    if not level:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Training level not found"
        )

    level_dict = {
        "id": level.id,
        "training_id": level.training_id,
        "level_name": level.level_name,
        "level_order": level.level_order,
        "description": level.description,
        "duration_hours": level.duration_hours,
        "prerequisites": level.prerequisites,
        "learning_objectives": level.learning_objectives,
        "learning_plan_links": level.learning_plan_links,
        "learning_materials": level.learning_materials,
        "pass_percentage": level.pass_percentage,
        "max_attempts": level.max_attempts,
        "exam_questions_count": level.exam_questions_count,
        "exam_duration_minutes": level.exam_duration_minutes,
        "created_at": level.created_at,
    }

    return level_dict


# ---------------- Assignment Endpoints ----------------
@app.post("/assignments", response_model=schemas.AssignmentOut)
def create_assignment(
    assignment: schemas.AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        # Validate dates
        today = datetime.utcnow().date()
        
        if assignment.training_start_date:
            if assignment.training_start_date < today:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Training start date cannot be in the past"
                )
        
        if assignment.training_end_date and assignment.training_start_date:
            if assignment.training_end_date < assignment.training_start_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Training end date cannot be before start date"
                )

        if assignment.level_dates:
            for level_id_str, dates in assignment.level_dates.items():
                start_d = dates.get('start_date')
                due_d = dates.get('due_date')
                
                if start_d and start_d < today:
                     raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Level start date cannot be in the past"
                    )
                
                if start_d and due_d and due_d < start_d:
                     raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Level due date cannot be before start date"
                    )

        # Check if user exists and is active
        user = (
            db.query(models.User)
            .filter(models.User.id == assignment.user_id, models.User.is_active == True)
            .first()
        )
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        # Check if training exists
        training = (
            db.query(models.Training)
            .filter(models.Training.id == assignment.training_id)
            .first()
        )
        if not training:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Training not found"
            )

        # Get the first level for this training
        first_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.training_id == assignment.training_id)
            .order_by(models.TrainingLevel.level_order.asc())
            .first()
        )

        if not first_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No levels found for this training",
            )

        # Check if user already has an assignment for this training
        existing_assignment = (
            db.query(models.Assignment)
            .filter(
                models.Assignment.user_id == assignment.user_id,
                models.Assignment.training_id == assignment.training_id,
            )
            .first()
        )

        if existing_assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already has an assignment for this training",
            )

        # Create new assignment
        new_assignment = models.Assignment(
            user_id=assignment.user_id,
            training_id=assignment.training_id,
            current_level_id=first_level.id,
            assigned_by=current_user.id,
            training_start_date=assignment.training_start_date,
            training_end_date=assignment.training_end_date,
            status=assignment.status,
            group_id=assignment.group_id,
        )

        db.add(new_assignment)
        db.flush()  # Flush to get the new_assignment.id without committing

        # Create level dates if provided
        if assignment.level_dates:
            for level_id_str, dates in assignment.level_dates.items():
                try:
                    level_id = int(level_id_str)
                    # Verify the level exists in this training
                    level_exists = db.query(models.TrainingLevel).filter(
                        models.TrainingLevel.id == level_id,
                        models.TrainingLevel.training_id == assignment.training_id
                    ).first()
                    
                    if level_exists:
                        level_date = models.AssignmentLevelDate(
                            assignment_id=new_assignment.id,
                            level_id=level_id,
                            start_date=dates.get('start_date'),
                            due_date=dates.get('due_date')
                        )
                        db.add(level_date)
                except (ValueError, TypeError):
                    # Skip invalid level IDs
                    continue

        db.commit()
        db.refresh(new_assignment)

        # Send email notification to employee
        try:
            email_service.send_training_assignment_email(
                employee_email=user.email,
                employee_name=user.full_name,
                training_title=training.title,
                training_description=training.description or "No description available",
                category=training.category,
                current_level=first_level.level_name,
                level_description=first_level.description or "No description available",
                duration_hours=first_level.duration_hours or 0,
                prerequisites=first_level.prerequisites or "None",
                learning_objectives=first_level.learning_objectives or "Not specified",
                training_start_date=assignment.training_start_date.strftime("%Y-%m-%d") if assignment.training_start_date else "Not specified",
                training_end_date=assignment.training_end_date.strftime("%Y-%m-%d") if assignment.training_end_date else "Not specified"
            )
        except Exception as email_error:
            print(f"Failed to send assignment email: {str(email_error)}")
            # Don't raise error - email failure shouldn't break assignment creation

        # Return assignment with related data
        assignment_dict = {
            "id": new_assignment.id,
            "user_id": new_assignment.user_id,
            "training_id": new_assignment.training_id,
            "current_level_id": new_assignment.current_level_id,
            "status": new_assignment.status,
            "training_start_date": new_assignment.training_start_date.date() if new_assignment.training_start_date else None,
            "training_end_date": new_assignment.training_end_date.date() if new_assignment.training_end_date else None,
            "actual_completion_date": new_assignment.actual_completion_date,
            "assigned_by": new_assignment.assigned_by,
            "updated_by": new_assignment.updated_by,
            "created_at": new_assignment.created_at,
            "updated_at": new_assignment.updated_at,
            "group_id": new_assignment.group_id,
            "user_name": new_assignment.user.full_name if new_assignment.user else None,
            "training_title": new_assignment.training.title if new_assignment.training else None,
            "current_level_name": new_assignment.current_level.level_name if new_assignment.current_level else None,
            "assigner_name": new_assignment.assigner.full_name if new_assignment.assigner else None,
            "updater_name": (
                new_assignment.updater.full_name if new_assignment.updater else None
            ),
            "group_name": new_assignment.group.name if new_assignment.group else None,
            "exam_questions_count": new_assignment.current_level.exam_questions_count if new_assignment.current_level else None,
            "exam_duration_minutes": new_assignment.current_level.exam_duration_minutes if new_assignment.current_level else None,
        }

        return assignment_dict

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating assignment: {str(e)}",
        )

@app.get("/assignments", response_model=List[schemas.AssignmentOut])
def list_assignments(
    skip: int = 0,
    #limit: int = 100,
    user_id: Optional[int] = None,
    training_id: Optional[int] = None,
    status: Optional[schemas.TrainingStatus] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Assignment)

    if current_user.role != models.UserRole.admin:
        query = query.filter(models.Assignment.user_id == current_user.id)
    elif user_id:
        query = query.filter(models.Assignment.user_id == user_id)

    if training_id:
        query = query.filter(models.Assignment.training_id == training_id)
    if status:
        query = query.filter(models.Assignment.status == status)

    assignments = query.offset(skip).all()

    result = []
    for assignment in assignments:
        assignment_dict = {
            "id": assignment.id,
            "user_id": assignment.user_id,
            "training_id": assignment.training_id,
            "current_level_id": assignment.current_level_id,
            "status": assignment.status,
            "training_start_date": assignment.training_start_date.date() if assignment.training_start_date else None,
            "training_end_date": assignment.training_end_date.date() if assignment.training_end_date else None,
            "actual_completion_date": assignment.actual_completion_date,
            "assigned_by": assignment.assigned_by,
            "updated_by": assignment.updated_by,
            "created_at": assignment.created_at,
            "updated_at": assignment.updated_at,
            "group_id": assignment.group_id,
            "user_name": assignment.user.full_name if assignment.user else None,
            "training_title": assignment.training.title if assignment.training else None,
            "current_level_name": assignment.current_level.level_name if assignment.current_level else None,
            "assigner_name": assignment.assigner.full_name if assignment.assigner else None,
            "updater_name": (
                assignment.updater.full_name if assignment.updater else None
            ),
            "group_name": assignment.group.name if assignment.group else None,
            "exam_questions_count": assignment.current_level.exam_questions_count if assignment.current_level else None,
            "exam_duration_minutes": assignment.current_level.exam_duration_minutes if assignment.current_level else None,
        }
        result.append(assignment_dict)

    return result


@app.get("/assignments/{assignment_id}", response_model=schemas.AssignmentOut)
def get_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    assignment = (
        db.query(models.Assignment)
        .filter(models.Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )

    if (
        current_user.role != models.UserRole.admin
        and assignment.user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )

    assignment_dict = {
        "id": assignment.id,
        "user_id": assignment.user_id,
        "training_id": assignment.training_id,
        "current_level_id": assignment.current_level_id,
        "status": assignment.status,
        "training_start_date": assignment.training_start_date.date() if assignment.training_start_date else None,
        "training_end_date": assignment.training_end_date.date() if assignment.training_end_date else None,
        "actual_completion_date": assignment.actual_completion_date,
        "assigned_by": assignment.assigned_by,
        "updated_by": assignment.updated_by,
        "group_id": assignment.group_id,
        "created_at": assignment.created_at,
        "updated_at": assignment.updated_at,
        "user_name": assignment.user.full_name if assignment.user else None,
        "training_title": assignment.training.title if assignment.training else None,
        "current_level_name": assignment.current_level.level_name if assignment.current_level else None,
        "assigner_name": assignment.assigner.full_name if assignment.assigner else None,
        "updater_name": assignment.updater.full_name if assignment.updater else None,
        "group_name": assignment.group.name if assignment.group else None,
        "exam_questions_count": assignment.current_level.exam_questions_count if assignment.current_level else None,
        "exam_duration_minutes": assignment.current_level.exam_duration_minutes if assignment.current_level else None,
    }

    return assignment_dict


@app.put("/assignments/{assignment_id}", response_model=schemas.AssignmentOut)
def update_assignment(
    assignment_id: int,
    assignment_update: schemas.AssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    db_assignment = (
        db.query(models.Assignment)
        .filter(models.Assignment.id == assignment_id)
        .first()
    )
    if not db_assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )

    update_data = assignment_update.dict(exclude_unset=True)

    # Handle level dates separately if provided
    level_dates = update_data.pop('level_dates', None)

    for key, value in update_data.items():
        setattr(db_assignment, key, value)

    db_assignment.updated_by = current_user.id
    db_assignment.updated_at = datetime.utcnow()

    # Update level dates if provided
    if level_dates is not None:
        # Delete existing level dates
        db.query(models.AssignmentLevelDate).filter(
            models.AssignmentLevelDate.assignment_id == assignment_id
        ).delete()

        # Create new level dates
        for level_id_str, dates in level_dates.items():
            try:
                level_id = int(level_id_str)
                # Verify the level exists in this training
                level_exists = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.id == level_id,
                    models.TrainingLevel.training_id == db_assignment.training_id
                ).first()
                
                if level_exists:
                    level_date = models.AssignmentLevelDate(
                        assignment_id=assignment_id,
                        level_id=level_id,
                        start_date=dates.get('start_date'),
                        due_date=dates.get('due_date')
                    )
                    db.add(level_date)
            except (ValueError, TypeError):
                # Skip invalid level IDs
                continue

    db.commit()
    db.refresh(db_assignment)

    assignment_dict = {
        "id": db_assignment.id,
        "user_id": db_assignment.user_id,
        "training_id": db_assignment.training_id,
        "current_level_id": db_assignment.current_level_id,
        "status": db_assignment.status,
        "training_start_date": db_assignment.training_start_date.date() if db_assignment.training_start_date else None,
        "training_end_date": db_assignment.training_end_date.date() if db_assignment.training_end_date else None,
        "actual_completion_date": db_assignment.actual_completion_date,
        "assigned_by": db_assignment.assigned_by,
        "updated_by": db_assignment.updated_by,
        "created_at": db_assignment.created_at,
        "updated_at": db_assignment.updated_at,
        "group_id": db_assignment.group_id,
        "user_name": db_assignment.user.full_name if db_assignment.user else None,
        "training_title": db_assignment.training.title if db_assignment.training else None,
        "current_level_name": db_assignment.current_level.level_name if db_assignment.current_level else None,
        "assigner_name": db_assignment.assigner.full_name if db_assignment.assigner else None,
        "updater_name": (
            db_assignment.updater.full_name if db_assignment.updater else None
        ),
        "group_name": db_assignment.group.name if db_assignment.group else None,
        "exam_questions_count": db_assignment.current_level.exam_questions_count if db_assignment.current_level else None,
        "exam_duration_minutes": db_assignment.current_level.exam_duration_minutes if db_assignment.current_level else None,
    }

    return assignment_dict

@app.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )
        
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        # First delete all evaluations for this assignment
        evaluations_to_delete = (
            db.query(models.Evaluation)
            .filter(models.Evaluation.assignment_id == assignment_id)
            .all()
        )
        
        for evaluation in evaluations_to_delete:
            db.delete(evaluation)

        # Delete any training progress records for this assignment
        progress_to_delete = (
            db.query(models.TrainingProgress)
            .filter(models.TrainingProgress.assignment_id == assignment_id)
            .all()
        )
        
        for progress in progress_to_delete:
            db.delete(progress)

        # Now delete the assignment
        db.delete(assignment)
        db.commit()

        return None

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting assignment: {str(e)}",
        )

@app.post("/groups/assign-training")
def assign_training_to_group(
    data: schemas.GroupTrainingAssign,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Assign training to all members of an employee group"""
    try:
        # Validate dates
        today = datetime.utcnow().date()
        
        if data.training_start_date:
            if data.training_start_date < today:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Training start date cannot be in the past"
                )

        if data.training_end_date and data.training_start_date:
            if data.training_end_date < data.training_start_date:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Training end date cannot be before start date"
                )

        if data.level_dates:
            for level_id_str, dates in data.level_dates.items():
                start_d = dates.get('start_date')
                due_d = dates.get('due_date')
                
                if start_d and start_d < today:
                     raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Level start date cannot be in the past"
                    )
                
                if start_d and due_d and due_d < start_d:
                     raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Level due date cannot be before start date"
                    )

        group = db.query(models.EmployeeGroup).filter(models.EmployeeGroup.id == data.group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        training = db.query(models.Training).filter(models.Training.id == data.training_id).first()
        if not training:
            raise HTTPException(status_code=404, detail="Training not found")
            
        first_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == data.training_id
        ).order_by(models.TrainingLevel.level_order.asc()).first()
        
        if not first_level:
            raise HTTPException(status_code=400, detail="Training has no levels")

        assignments_created = 0
        
        for member in group.members:
            # Check if user exists and is active
            user = db.query(models.User).filter(models.User.id == member.employee_id, models.User.is_active == True).first()
            if not user:
                continue

            # Check if already assigned
            existing = db.query(models.Assignment).filter(
                models.Assignment.user_id == member.employee_id,
                models.Assignment.training_id == data.training_id
            ).first()
            
            if existing:
                continue
                
            new_assignment = models.Assignment(
                user_id=member.employee_id,
                training_id=data.training_id,
                current_level_id=first_level.id,
                assigned_by=current_user.id,
                group_id=data.group_id,
                training_start_date=data.training_start_date,
                training_end_date=data.training_end_date,
                status=models.TrainingStatus.assigned
            )
            db.add(new_assignment)
            db.flush()
            
            # Add level dates if provided
            if data.level_dates:
                for level_id_str, dates in data.level_dates.items():
                    try:
                        level_id = int(level_id_str)
                        level_exists = db.query(models.TrainingLevel).filter(
                            models.TrainingLevel.id == level_id,
                            models.TrainingLevel.training_id == data.training_id
                        ).first()
                        if level_exists:
                            ld = models.AssignmentLevelDate(
                                assignment_id=new_assignment.id,
                                level_id=level_id,
                                start_date=dates.get('start_date'),
                                due_date=dates.get('due_date')
                            )
                            db.add(ld)
                    except:
                        continue
            
            assignments_created += 1
            
            # Optional: Add progress record
            
            # Send email notification
            try:
                email_service.send_training_assignment_email(
                    employee_email=user.email,
                    employee_name=user.full_name,
                    training_title=training.title,
                    training_description=training.description or "No description available",
                    category=training.category,
                    current_level=first_level.level_name,
                    level_description=first_level.description or "No description available",
                    duration_hours=first_level.duration_hours or 0,
                    prerequisites=first_level.prerequisites or "None",
                    learning_objectives=first_level.learning_objectives or "Not specified",
                    training_start_date=data.training_start_date.strftime("%Y-%m-%d") if data.training_start_date else "Not specified",
                    training_end_date=data.training_end_date.strftime("%Y-%m-%d") if data.training_end_date else "Not specified"
                )
            except Exception as email_error:
                print(f"Failed to send email to {user.email}: {str(email_error)}")

        db.commit()
        return {"message": f"Successfully created {assignments_created} assignments", "count": assignments_created}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error assigning training to group: {str(e)}")

# ---------------- New Assignment Endpoints ----------------
@app.put("/assignments/{assignment_id}/score")
def update_assignment_score(
    assignment_id: int,
    score_data: dict,  # Expecting {"score": float, "level": str}
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        # Get the current training level
        current_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.id == assignment.current_level_id)
            .first()
        )

        if not current_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Training level not found"
            )

        # Create or update evaluation
        evaluation = (
            db.query(models.Evaluation)
            .filter(
                models.Evaluation.assignment_id == assignment_id,
                models.Evaluation.training_level_id == assignment.current_level_id,
            )
            .first()
        )

        score = score_data.get("score", 0)
        level_name = score_data.get("level", "").lower()

        if evaluation:
            # Update existing evaluation
            evaluation.total_score = score
            evaluation.evaluation_date = datetime.utcnow()
            evaluation.evaluated_by = current_user.id

            if score >= current_level.pass_percentage:
                evaluation.status = models.EvaluationStatus.passed
            else:
                evaluation.status = models.EvaluationStatus.failed
        else:
            # Create new evaluation
            evaluation = models.Evaluation(
                assignment_id=assignment_id,
                training_level_id=assignment.current_level_id,
                attempt_number=1,
                evaluation_date=datetime.utcnow(),
                total_score=score,
                max_possible_score=100.0,
                evaluated_by=current_user.id,
                status=(
                    models.EvaluationStatus.passed
                    if score >= current_level.pass_percentage
                    else models.EvaluationStatus.failed
                ),
            )
            db.add(evaluation)

        # Update assignment status if score meets passing criteria
        if score >= current_level.pass_percentage:
            # Check if this is the final level
            training_levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == assignment.training_id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            current_level_index = next(
                (
                    i
                    for i, level in enumerate(training_levels)
                    if level.id == assignment.current_level_id
                ),
                -1,
            )

            if (
                current_level_index >= 0
                and current_level_index < len(training_levels) - 1
            ):
                # Move to next level
                next_level = training_levels[current_level_index + 1]
                assignment.current_level_id = next_level.id
                assignment.status = models.TrainingStatus.assigned
            else:
                # This is the final level - mark assignment as completed
                assignment.status = models.TrainingStatus.completed
                assignment.actual_completion_date = datetime.utcnow()

        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        db.commit()

        return {
            "message": "Score updated successfully",
            "assignment_id": assignment_id,
            "score": score,
            "new_status": assignment.status,
            "current_level": current_level.level_name,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating score: {str(e)}",
        )


@app.put("/assignments/{assignment_id}/progress-level")
def progress_to_next_level(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        # Get all levels for this training
        training_levels = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.training_id == assignment.training_id)
            .order_by(models.TrainingLevel.level_order.asc())
            .all()
        )

        if not training_levels:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No training levels found"
            )

        # Find current level index
        current_level_index = next(
            (
                i
                for i, level in enumerate(training_levels)
                if level.id == assignment.current_level_id
            ),
            -1,
        )

        if current_level_index == -1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Current level not found"
            )

        # Store previous level for email
        previous_level = training_levels[current_level_index]

        # Check if there's a next level
        if current_level_index < len(training_levels) - 1:
            next_level = training_levels[current_level_index + 1]
            assignment.current_level_id = next_level.id
            assignment.status = models.TrainingStatus.assigned
            assignment.updated_by = current_user.id
            assignment.updated_at = datetime.utcnow()

            db.commit()

            # Send level progression email
            try:
                email_service.send_level_progression_email(
                    employee_email=assignment.user.email,
                    employee_name=assignment.user.full_name,
                    training_title=assignment.training.title,
                    previous_level=previous_level.level_name,
                    new_level=next_level.level_name,
                    new_level_description=next_level.description or "No description available",
                    duration_hours=next_level.duration_hours or 0,
                    prerequisites=next_level.prerequisites or "None",
                    learning_objectives=next_level.learning_objectives or "Not specified"
                )
            except Exception as email_error:
                print(f"Failed to send progression email: {str(email_error)}")
                # Don't raise error - email failure shouldn't break progression

            return {
                "message": "Progressed to next level successfully",
                "assignment_id": assignment_id,
                "previous_level": previous_level.level_name,
                "new_level": next_level.level_name,
                "new_status": assignment.status,
            }
        else:
            # Already at the final level
            assignment.status = models.TrainingStatus.completed
            assignment.actual_completion_date = datetime.utcnow()
            assignment.updated_by = current_user.id
            assignment.updated_at = datetime.utcnow()

            db.commit()

            return {
                "message": "Assignment completed - no more levels",
                "assignment_id": assignment_id,
                "status": assignment.status,
            }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error progressing to next level: {str(e)}",
        )

@app.put("/assignments/{assignment_id}/complete-level")
def complete_current_level(
    assignment_id: int,
    score_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Complete the current level with detailed scores"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        current_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.id == assignment.current_level_id)
            .first()
        )

        if not current_level:
            raise HTTPException(status_code=404, detail="Current level not found")

        # Store current level for email
        previous_level = current_level

        # Extract scores and handle empty values - default to 0
        mcq_score = score_data.get("mcq_score")
        assignment_score = score_data.get("assignment_score")
        force_complete = score_data.get("force_complete", False)

        # Convert None or empty string to 0
        if mcq_score is None or mcq_score == "":
            mcq_score = 0
        if assignment_score is None or assignment_score == "":
            assignment_score = 0

        # Ensure scores are numeric
        try:
            mcq_score = float(mcq_score)
            assignment_score = float(assignment_score)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Scores must be numeric values")

        # *** VALIDATION: Ensure all scores are within 0-100 range ***
        for score_name, score_value in [
            ("mcq_score", mcq_score),
            ("assignment_score", assignment_score),
        ]:
            if score_value is not None:
                if score_value < 0 or score_value > 100:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{score_name} must be between 0 and 100, got {score_value}",
                    )

        # *** AUTO-CALCULATE total score from MCQ and assignment scores ***
        total_score = None
        if mcq_score is not None and assignment_score is not None:
            total_score = (mcq_score + assignment_score) / 2
        elif mcq_score is not None:
            total_score = mcq_score
        elif assignment_score is not None:
            total_score = assignment_score

        # Default to 100 if no scores provided and force complete
        if total_score is None and force_complete:
            total_score = 100

        # Delete any existing evaluation for this level to prevent duplicates
        db.query(models.Evaluation).filter(
            models.Evaluation.assignment_id == assignment_id,
            models.Evaluation.training_level_id == assignment.current_level_id,
        ).delete()

        # Create new evaluation
        evaluation = models.Evaluation(
            assignment_id=assignment_id,
            training_level_id=assignment.current_level_id,
            attempt_number=1,
            evaluation_date=datetime.utcnow(),
            mcq_score=float(mcq_score) if mcq_score is not None else None,
            assignment_score=(
                float(assignment_score) if assignment_score is not None else None
            ),
            total_score=float(total_score) if total_score is not None else None,
            max_possible_score=100.0,
            evaluated_by=current_user.id,
            status=models.EvaluationStatus.not_started,
        )
        db.add(evaluation)

        # Determine if level is passed
        passing_score = current_level.pass_percentage
        is_passed = force_complete or (
            total_score is not None and total_score >= passing_score
        )

        if is_passed:
            evaluation.status = models.EvaluationStatus.passed

            # Progress to next level or complete assignment
            training_levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == assignment.training_id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            current_level_index = next(
                (
                    i
                    for i, level in enumerate(training_levels)
                    if level.id == assignment.current_level_id
                ),
                -1,
            )

            if (
                current_level_index >= 0
                and current_level_index < len(training_levels) - 1
            ):
                next_level = training_levels[current_level_index + 1]
                assignment.current_level_id = next_level.id
                assignment.status = models.TrainingStatus.assigned
                message = f"Level completed. Progressed to {next_level.level_name}"
                
                # Send level progression email
                try:
                    email_service.send_level_progression_email(
                        employee_email=assignment.user.email,
                        employee_name=assignment.user.full_name,
                        training_title=assignment.training.title,
                        previous_level=previous_level.level_name,
                        new_level=next_level.level_name,
                        new_level_description=next_level.description or "No description available",
                        duration_hours=next_level.duration_hours or 0,
                        prerequisites=next_level.prerequisites or "None",
                        learning_objectives=next_level.learning_objectives or "Not specified"
                    )
                except Exception as email_error:
                    print(f"Failed to send progression email: {str(email_error)}")
            else:
                assignment.status = models.TrainingStatus.completed
                assignment.actual_completion_date = datetime.utcnow()
                message = "Final level completed. Assignment marked as completed."
        else:
            evaluation.status = models.EvaluationStatus.failed
            message = "Level completed but score below passing criteria."

        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        db.commit()

        return {
            "message": message,
            "assignment_id": assignment_id,
            "scores": {
                "mcq_score": mcq_score,
                "assignment_score": assignment_score,
                "total_score": total_score,
            },
            "status": assignment.status,
            "current_level": current_level.level_name,
            "passed": evaluation.status == models.EvaluationStatus.passed,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error completing level: {str(e)}",
        )

@app.put("/assignments/{assignment_id}/update-scores")
def update_assignment_scores(
    assignment_id: int,
    score_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Simple endpoint to update scores without level progression"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Handle empty scores - default to 0
        mcq_score = score_data.get("mcq_score")
        assignment_score = score_data.get("assignment_score")

        # Convert None or empty string to 0
        if mcq_score is None or mcq_score == "":
            mcq_score = 0
        if assignment_score is None or assignment_score == "":
            assignment_score = 0

        # Ensure scores are numeric
        try:
            mcq_score = float(mcq_score) if mcq_score is not None else None
            assignment_score = (
                float(assignment_score) if assignment_score is not None else None
            )
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Scores must be numeric values")

        # *** VALIDATION: Ensure all scores are within 0-100 range ***
        for score_name, score_value in [
            ("mcq_score", mcq_score),
            ("assignment_score", assignment_score),
        ]:
            if score_value is not None and (score_value < 0 or score_value > 100):
                raise HTTPException(
                    status_code=400,
                    detail=f"{score_name} must be between 0 and 100, got {score_value}",
                )

        # Delete existing evaluation to prevent duplicates
        existing_eval = (
            db.query(models.Evaluation)
            .filter(
                models.Evaluation.assignment_id == assignment_id,
                models.Evaluation.training_level_id == assignment.current_level_id,
            )
            .first()
        )

        if existing_eval:
            db.delete(existing_eval)
            db.flush()

        # Create new evaluation
        evaluation = models.Evaluation(
            assignment_id=assignment_id,
            training_level_id=assignment.current_level_id,
            attempt_number=1,
            evaluation_date=datetime.utcnow(),
            max_possible_score=100.0,
            evaluated_by=current_user.id,
            status=models.EvaluationStatus.not_started,
        )

        # Update scores
        if mcq_score is not None:
            evaluation.mcq_score = mcq_score
        if assignment_score is not None:
            evaluation.assignment_score = assignment_score

        # *** AUTO-CALCULATE total from MCQ and assignment scores ***
        if evaluation.mcq_score is not None and evaluation.assignment_score is not None:
            evaluation.total_score = (
                evaluation.mcq_score + evaluation.assignment_score
            ) / 2
        elif evaluation.mcq_score is not None:
            evaluation.total_score = evaluation.mcq_score
        elif evaluation.assignment_score is not None:
            evaluation.total_score = evaluation.assignment_score

        db.add(evaluation)
        db.commit()

        return {
            "message": "Scores updated successfully",
            "assignment_id": assignment_id,
            "scores": {
                "mcq_score": evaluation.mcq_score,
                "assignment_score": evaluation.assignment_score,
                "total_score": evaluation.total_score,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating scores: {str(e)}")


@app.put("/assignments/{assignment_id}/reset-level")
def reset_assignment_level(
    assignment_id: int,
    reset_data: dict = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Reset assignment with options for full reset or level reset, including marks and attempts"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Parse reset options
        reset_type = reset_data.get("reset_type", "level")  # "level" or "full"
        target_level_id = reset_data.get("target_level_id")
        reset_marks = reset_data.get("reset_marks", True)
        reset_attempts = reset_data.get("reset_attempts", True)
        keep_scores = reset_data.get("keep_scores", False)
        send_email = reset_data.get("send_email", False)

        training_levels = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.training_id == assignment.training_id)
            .order_by(models.TrainingLevel.level_order.asc())
            .all()
        )

        if not training_levels:
            raise HTTPException(status_code=404, detail="No training levels found")

        # Determine target level
        if reset_type == "full" or not target_level_id:
            # Reset to first level for full reset
            target_level_id = training_levels[0].id
        else:
            # Verify target level exists in this training
            target_level = next(
                (level for level in training_levels if level.id == target_level_id), None
            )
            if not target_level:
                raise HTTPException(status_code=400, detail="Invalid target level")

        target_level = next(level for level in training_levels if level.id == target_level_id)

        # Store previous state for email
        previous_level_name = assignment.current_level.level_name
        previous_status = assignment.status

        # *** CRITICAL FIX: Always reset assignment status to 'assigned' when resetting ***
        assignment.current_level_id = target_level_id
        assignment.status = models.TrainingStatus.assigned  # Always reset to assigned
        assignment.actual_completion_date = None
        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        # Reset evaluations based on options
        if reset_marks or reset_type == "full":
            if reset_type == "full":
                # Delete ALL evaluations for this assignment
                evaluations = (
                    db.query(models.Evaluation)
                    .filter(models.Evaluation.assignment_id == assignment_id)
                    .all()
                )
            else:
                # Delete evaluations only for levels from target level onwards
                target_level_order = target_level.level_order
                levels_to_reset = [
                    level.id for level in training_levels 
                    if level.level_order >= target_level_order
                ]
                
                evaluations = (
                    db.query(models.Evaluation)
                    .filter(
                        models.Evaluation.assignment_id == assignment_id,
                        models.Evaluation.training_level_id.in_(levels_to_reset)
                    )
                    .all()
                )

            for eval in evaluations:
                db.delete(eval)

        # Reset exam attempts based on options
        if reset_attempts or reset_type == "full":
            if reset_type == "full":
                # Delete ALL exam attempts for this assignment
                exam_attempts = (
                    db.query(models.MCQExamAttempt)
                    .filter(models.MCQExamAttempt.assignment_id == assignment_id)
                    .all()
                )
            else:
                # Delete exam attempts only for levels from target level onwards
                target_level_order = target_level.level_order
                levels_to_reset = [
                    level.id for level in training_levels 
                    if level.level_order >= target_level_order
                ]
                
                exam_attempts = (
                    db.query(models.MCQExamAttempt)
                    .filter(
                        models.MCQExamAttempt.assignment_id == assignment_id,
                        models.MCQExamAttempt.training_level_id.in_(levels_to_reset)
                    )
                    .all()
                )

            for attempt in exam_attempts:
                # Delete associated answers first
                db.query(models.MCQAnswer).filter(
                    models.MCQAnswer.exam_attempt_id == attempt.id
                ).delete()
                db.delete(attempt)

        db.commit()

        # Send notification email if requested
        if send_email:
            try:
                email_service.send_reset_notification_email(
                    employee_email=assignment.user.email,
                    employee_name=assignment.user.full_name,
                    training_title=assignment.training.title,
                    previous_level=previous_level_name,
                    new_level=target_level.level_name,
                    reset_type=reset_type,
                    reset_marks=reset_marks,
                    reset_attempts=reset_attempts
                )
            except Exception as email_error:
                print(f"Failed to send reset email: {str(email_error)}")

        return {
            "message": f"Assignment reset successfully to {target_level.level_name}",
            "assignment_id": assignment_id,
            "reset_type": reset_type,
            "previous_level": previous_level_name,
            "new_level": target_level.level_name,
            "previous_status": previous_status,
            "new_status": assignment.status,  # Should always be 'assigned'
            "reset_marks": reset_marks,
            "reset_attempts": reset_attempts
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error resetting assignment: {str(e)}")
@app.get("/assignments/{assignment_id}/evaluations-detailed")
def get_assignment_evaluations_detailed(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed evaluations for an assignment"""
    assignment = (
        db.query(models.Assignment)
        .filter(models.Assignment.id == assignment_id)
        .first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if (
        current_user.role != models.UserRole.admin
        and assignment.user_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    evaluations = (
        db.query(models.Evaluation)
        .filter(models.Evaluation.assignment_id == assignment_id)
        .order_by(models.Evaluation.evaluation_date.desc())
        .all()
    )

    result = []
    for eval in evaluations:
        result.append(
            {
                "id": eval.id,
                "assignment_id": eval.assignment_id,
                "training_level_id": eval.training_level_id,
                "level_name": eval.training_level.level_name,
                "attempt_number": eval.attempt_number,
                "evaluation_date": eval.evaluation_date,
                "mcq_score": eval.mcq_score,
                "practical_score": eval.practical_score,
                "assignment_score": eval.assignment_score,
                "total_score": eval.total_score,
                "max_possible_score": eval.max_possible_score,
                "percentage_score": eval.percentage_score,
                "is_passing_score": eval.is_passing_score,
                "status": eval.status,
                "comments": eval.comments,
                "evaluated_by": eval.evaluated_by,
                "evaluator_name": eval.evaluator.full_name,
                "created_at": eval.created_at,
            }
        )

    return result


# ---------------- Evaluation Endpoints ----------------
@app.post("/evaluations", response_model=schemas.EvaluationOut)
def create_evaluation(
    evaluation: schemas.EvaluationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == evaluation.assignment_id)
            .first()
        )
        if not assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        training_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.id == evaluation.training_level_id)
            .first()
        )
        if not training_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Training level not found"
            )

        existing_attempts = (
            db.query(models.Evaluation)
            .filter(models.Evaluation.assignment_id == evaluation.assignment_id)
            .count()
        )

        if existing_attempts >= training_level.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum attempts ({training_level.max_attempts}) exceeded",
            )

        if evaluation.total_score is None:
            scores = {
                "mcq_score": evaluation.mcq_score or 0,
                "practical_score": evaluation.practical_score or 0,
                "assignment_score": evaluation.assignment_score or 0,
            }
            total_score = utils.calculate_overall_score(scores)
        else:
            total_score = evaluation.total_score

        if total_score >= training_level.pass_percentage:
            status = models.EvaluationStatus.passed
            assignment.status = models.TrainingStatus.completed
            assignment.actual_completion_date = datetime.utcnow()
        else:
            status = models.EvaluationStatus.failed
            if existing_attempts + 1 >= training_level.max_attempts:
                assignment.status = models.TrainingStatus.failed

        evaluation_data = evaluation.dict(exclude={"total_score"})
        new_evaluation = models.Evaluation(
            **evaluation_data,
            total_score=total_score,
            status=status,
            evaluated_by=current_user.id,
        )

        db.add(new_evaluation)
        db.commit()
        db.refresh(new_evaluation)

        return new_evaluation

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating evaluation: {str(e)}",
        )


@app.put("/assignments/{assignment_id}/update-score")
def update_assignment_score_simple(
    assignment_id: int,
    score_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Simple endpoint to just update the score without level progression logic"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        score = score_data.get("score")
        if score is None:
            raise HTTPException(status_code=400, detail="Score is required")

        # Find or create evaluation
        evaluation = (
            db.query(models.Evaluation)
            .filter(
                models.Evaluation.assignment_id == assignment_id,
                models.Evaluation.training_level_id == assignment.current_level_id,
            )
            .first()
        )

        if evaluation:
            evaluation.total_score = score
            evaluation.evaluation_date = datetime.utcnow()
            evaluation.evaluated_by = current_user.id
        else:
            evaluation = models.Evaluation(
                assignment_id=assignment_id,
                training_level_id=assignment.current_level_id,
                attempt_number=1,
                evaluation_date=datetime.utcnow(),
                total_score=score,
                max_possible_score=100.0,
                evaluated_by=current_user.id,
                status=(
                    models.EvaluationStatus.passed
                    if score >= 60
                    else models.EvaluationStatus.failed
                ),
            )
            db.add(evaluation)

        db.commit()

        return {
            "message": "Score updated successfully",
            "assignment_id": assignment_id,
            "score": score,
            "evaluation_id": evaluation.id,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating score: {str(e)}")


@app.get("/evaluations", response_model=List[schemas.EvaluationOut])
def list_evaluations(
    assignment_id: Optional[int] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Evaluation)

    if assignment_id:
        query = query.filter(models.Evaluation.assignment_id == assignment_id)

    if user_id:
        if current_user.role != models.UserRole.admin and user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        query = query.join(models.Assignment).filter(
            models.Assignment.user_id == user_id
        )
    elif current_user.role != models.UserRole.admin:
        query = query.join(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        )

    evaluations = query.all()

    result = []
    for evaluation in evaluations:
        evaluation_dict = evaluation.__dict__.copy()
        evaluation_dict["evaluator_name"] = evaluation.evaluator.full_name
        evaluation_dict["level_name"] = evaluation.training_level.level_name
        result.append(evaluation_dict)

    return result


# ---------------- Analytics Report Endpoints ----------------
@app.get("/reports/overview", response_model=schemas.OverviewReport)
def get_overview_report(
    db: Session = Depends(get_db), 
    current_user: models.User = Depends(get_current_user)
):
    try:
        # Count active employees (excluding admins)
        total_employees = (
            db.query(models.User)
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .count()
        )

        # Count total training programs
        total_trainings = db.query(models.Training).count()

        # Count total assignments (training enrollments)
        total_assignments = db.query(models.Assignment).count()

        # Count assignments by status
        status_counts = (
            db.query(
                models.Assignment.status,
                func.count(models.Assignment.id).label('count')
            )
            .group_by(models.Assignment.status)
            .all()
        )

        # Convert to dictionary for easy access
        status_dict = {status: count for status, count in status_counts}
        
        assigned_assignments = status_dict.get(models.TrainingStatus.assigned, 0)
        in_progress_assignments = status_dict.get(models.TrainingStatus.in_progress, 0)
        completed_assignments = status_dict.get(models.TrainingStatus.completed, 0)
        failed_assignments = status_dict.get(models.TrainingStatus.failed, 0)

        # Calculate completion rate based on assignments
        completion_rate = round(
            (completed_assignments / total_assignments * 100) if total_assignments > 0 else 0, 
            2
        )

        # Calculate progress rate (completed + in progress)
        progress_rate = round(
            ((completed_assignments + in_progress_assignments) / total_assignments * 100) 
            if total_assignments > 0 else 0, 
            2
        )

        # Get average score across all evaluations
        avg_score_result = (
            db.query(func.avg(models.Evaluation.total_score))
            .filter(
                models.Evaluation.total_score >= 0,
                models.Evaluation.total_score <= 100
            )
            .scalar()
        )
        avg_score = round(avg_score_result or 0, 2)

        # Count employees with active assignments
        employees_with_assignments = (
            db.query(func.count(func.distinct(models.Assignment.user_id)))
            .scalar()
        )

        # Calculate employee engagement rate
        engagement_rate = round(
            (employees_with_assignments / total_employees * 100) if total_employees > 0 else 0, 
            2
        )

        # Get recent activity (assignments created in last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_activity = (
            db.query(func.count(models.Assignment.id))
            .filter(models.Assignment.created_at >= thirty_days_ago)
            .scalar()
        )

        # Get department coverage
        departments_with_assignments = (
            db.query(func.count(func.distinct(models.User.department)))
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .scalar()
        )

        # Get category distribution
        total_categories = (
            db.query(func.count(func.distinct(models.Training.category)))
            .scalar()
        )

        return {
            "total_employees": total_employees,
            "total_trainings": total_trainings,
            "total_assignments": total_assignments,
            "assigned_assignments": assigned_assignments,
            "in_progress_assignments": in_progress_assignments,
            "completed_assignments": completed_assignments,
            "failed_assignments": failed_assignments,
            "completion_rate": completion_rate,
            "progress_rate": progress_rate,
            "average_score": avg_score,
            "employees_with_assignments": employees_with_assignments,
            "engagement_rate": engagement_rate,
            "recent_activity": recent_activity,
            "departments_with_assignments": departments_with_assignments,
            "total_categories": total_categories,
            "success_rate": round(100 - (failed_assignments / total_assignments * 100) if total_assignments > 0 else 100, 2)
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating overview report: {str(e)}",
        )



@app.get("/reports/top-employees")
def get_top_employees(
   # limit: int = 5,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        # Get employees with their training completion counts and average scores
        top_employees = (
            db.query(
                models.User.id.label("employee_id"),
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
                # Count distinct trainings assigned to each employee
                func.count(func.distinct(models.Assignment.training_id)).label("total_trainings"),
                # Count distinct completed trainings
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.training_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_trainings"),
                # Calculate average score across all evaluations
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .group_by(
                models.User.id,
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
            )
            .having(func.count(func.distinct(models.Assignment.training_id)) > 0)  # Only employees with trainings
            .order_by(
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.training_id,
                            ),
                            else_=None,
                        )
                    )
                ).desc(),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).desc().nullslast(),
            )
            .all()
        )

        result = []
        for emp in top_employees:
            avg_score = emp.avg_score or 0
            result.append(
                {
                    "employee_id": emp.employee_id,
                    "name": f"{emp.first_name} {emp.last_name}",
                    "designation": emp.designation or "Employee",
                    "total_courses": emp.total_trainings or 0,  # This now represents trainings
                    "completed_courses": emp.completed_trainings or 0,  # Completed trainings
                    "avg_score": round(avg_score, 2),
                }
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating top employees report: {str(e)}",
        )

@app.get("/reports/top-employees")
def get_top_employees(
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        # *** FIX: Only consider valid scores (0-100) ***
        top_employees = (
            db.query(
                models.User.id.label("employee_id"),
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
                func.count(models.Assignment.id).label("total_assignments"),
                func.sum(
                    case(
                        (
                            models.Assignment.status == models.TrainingStatus.completed,
                            1,
                        ),
                        else_=0,
                    )
                ).label("completed"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .group_by(
                models.User.id,
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
            )
            .order_by(
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                )
                .desc()
                .nullslast(),
                func.sum(
                    case(
                        (
                            models.Assignment.status == models.TrainingStatus.completed,
                            1,
                        ),
                        else_=0,
                    )
                ).desc(),
            )
            .limit(limit)
            .all()
        )

        result = []
        for emp in top_employees:
            avg_score = emp.avg_score or 0
            result.append(
                {
                    "employee_id": emp.employee_id,
                    "name": f"{emp.first_name} {emp.last_name}",
                    "designation": emp.designation or "Employee",
                    "completed": emp.completed or 0,
                    "total_assignments": emp.total_assignments or 0,
                    "avg_score": round(avg_score, 2),
                }
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating top employees report: {str(e)}",
        )


@app.get("/reports/top-trainings")
def get_top_trainings(
    #limit: int = 5,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        # Get trainings with unique employee counts and completion rates
        top_trainings = (
            db.query(
                models.Training.id.label("training_id"),
                models.Training.title,
                models.Training.category,
                # Count unique employees assigned to this training
                func.count(func.distinct(models.Assignment.user_id)).label("assigned_employees"),
                # Count unique employees who completed this training
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_employees"),
                # Calculate average score for this training
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(
                models.Assignment, models.Assignment.training_id == models.Training.id
            )
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .group_by(
                models.Training.id, models.Training.title, models.Training.category
            )
            .order_by(
                func.count(func.distinct(models.Assignment.user_id)).desc(),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).desc(),
            )
            #.limit(limit)
            .all()
        )

        result = []
        for training in top_trainings:
            completed_employees = training.completed_employees or 0
            assigned_employees = training.assigned_employees or 0
            completion_rate = round(
                (completed_employees / assigned_employees * 100) if assigned_employees > 0 else 0, 2
            )
            avg_score = training.avg_score or 0

            result.append(
                {
                    "training_id": training.training_id,
                    "title": training.title,
                    "category": training.category,
                    "assigned_employees": assigned_employees,
                    "completed_employees": completed_employees,
                    "completion_rate": completion_rate,
                    "avg_score": round(avg_score, 2),
                }
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating top trainings report: {str(e)}",
        )


@app.get("/reports/category-performance")
def get_category_performance(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    try:
        # Get category performance with employee counts
        category_performance = (
            db.query(
                models.Training.category,
                # Count unique employees in this category
                func.count(func.distinct(models.Assignment.user_id)).label("total_employees"),
                # Count unique employees who completed any training in this category
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_employees"),
                # Calculate average score for this category
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(
                models.Assignment, models.Assignment.training_id == models.Training.id
            )
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .group_by(models.Training.category)
            .order_by(
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                )
                .desc()
                .nullslast()
            )
            .all()
        )

        result = []
        for cat in category_performance:
            completed_employees = cat.completed_employees or 0
            total_employees = cat.total_employees or 0
            completion_rate = round(
                (completed_employees / total_employees * 100) if total_employees > 0 else 0, 2
            )
            avg_score = cat.avg_score or 0

            result.append(
                {
                    "category": cat.category,
                    "total_employees": total_employees,
                    "completed_employees": completed_employees,
                    "completion_rate": completion_rate,
                    "avg_score": round(avg_score, 2),
                }
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating category performance report: {str(e)}",
        )


@app.get("/reports/department-performance")
def get_department_performance(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    try:
        # Get department performance with training and employee counts
        department_performance = (
            db.query(
                models.User.department,
                # Count unique trainings assigned to this department
                func.count(func.distinct(models.Assignment.training_id)).label("total_trainings"),
                # Count unique completed trainings in this department
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.training_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_trainings"),
                # Count unique employees in this department
                func.count(func.distinct(models.Assignment.user_id)).label("total_employees"),
                # Count unique employees who completed any training
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_employees"),
                # Calculate average score for this department
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .filter(models.User.is_active == True)
            .group_by(models.User.department)
            .order_by(
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.training_id,
                            ),
                            else_=None,
                        )
                    )
                ).desc()
            )
            .all()
        )

        result = []
        for dept in department_performance:
            completed_trainings = dept.completed_trainings or 0
            total_trainings = dept.total_trainings or 0
            completion_rate = round(
                (completed_trainings / total_trainings * 100) if total_trainings > 0 else 0, 2
            )
            avg_score = dept.avg_score or 0

            result.append(
                {
                    "department": dept.department,
                    "total_trainings": total_trainings,
                    "completed_trainings": completed_trainings,
                    "total_employees": dept.total_employees or 0,
                    "completed_employees": dept.completed_employees or 0,
                    "completion_rate": completion_rate,
                    "avg_score": round(avg_score, 2),
                }
            )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating department performance report: {str(e)}",
        )



# Add this endpoint in the Assignment Endpoints section
@app.put(
    "/assignments/{assignment_id}/update-status", response_model=schemas.AssignmentOut
)
def update_assignment_status(
    assignment_id: int,
    status_update: dict,  # Expecting {"status": TrainingStatus}
    db: Session = Depends(get_db),
    current_user: models.User = Depends(
        get_current_user
    ),  # Changed from require_role to get_current_user
):
    """
    Allow employees to update their own assignment status
    """
    try:
        # Find the assignment
        db_assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not db_assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        # Check if the current user owns this assignment or is admin
        if (
            current_user.role != models.UserRole.admin
            and db_assignment.user_id != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only update your own assignments",
            )

        # Validate status
        new_status = status_update.get("status")
        if new_status not in [status.value for status in models.TrainingStatus]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {[status.value for status in models.TrainingStatus]}",
            )

        # Update the assignment
        db_assignment.status = models.TrainingStatus(new_status)
        db_assignment.updated_by = current_user.id
        db_assignment.updated_at = datetime.utcnow()

        # If marking as completed and no completion date set, set it
        if new_status == "completed" and not db_assignment.actual_completion_date:
            db_assignment.actual_completion_date = datetime.utcnow()

        db.commit()
        db.refresh(db_assignment)

        # Return updated assignment with related data
        assignment_dict = {
            "id": db_assignment.id,
            "user_id": db_assignment.user_id,
            "training_id": db_assignment.training_id,
            "current_level_id": db_assignment.current_level_id,
            "status": db_assignment.status,
            "training_start_date": db_assignment.training_start_date,
            "training_end_date": db_assignment.training_end_date,
            "actual_completion_date": db_assignment.actual_completion_date,
            "assigned_by": db_assignment.assigned_by,
            "updated_by": db_assignment.updated_by,
            "created_at": db_assignment.created_at,
            "updated_at": db_assignment.updated_at,
            "user_name": db_assignment.user.full_name,
            "training_title": db_assignment.training.title,
            "current_level_name": db_assignment.current_level.level_name,
            "assigner_name": db_assignment.assigner.full_name,
            "updater_name": (
                db_assignment.updater.full_name if db_assignment.updater else None
            ),
        }

        return assignment_dict

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating assignment status: {str(e)}",
        )


@app.get("/reports/training-details")
def get_training_details_report(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """
    Get detailed training statistics showing actual enrolled employees
    """
    try:
        # Get all trainings with basic info
        trainings = db.query(models.Training).all()

        result = []

        for training in trainings:
            # Get all assignments for this training
            assignments = (
                db.query(models.Assignment)
                .filter(models.Assignment.training_id == training.id)
                .all()
            )

            # Get enrolled employees (only employees, not admins)
            enrolled_employees = []
            total_enrolled = 0
            completed_count = 0
            in_progress_count = 0
            assigned_count = 0
            failed_count = 0

            for assignment in assignments:
                user = (
                    db.query(models.User)
                    .filter(
                        models.User.id == assignment.user_id,
                        models.User.is_active == True,
                        models.User.role
                        == models.UserRole.employee,  # Only count employees
                    )
                    .first()
                )

                if user:
                    total_enrolled += 1

                    # Track status counts
                    if assignment.status == models.TrainingStatus.completed:
                        completed_count += 1
                    elif assignment.status == models.TrainingStatus.in_progress:
                        in_progress_count += 1
                    elif assignment.status == models.TrainingStatus.assigned:
                        assigned_count += 1
                    elif assignment.status == models.TrainingStatus.failed:
                        failed_count += 1

                    # Get evaluation scores for this assignment
                    evaluations = (
                        db.query(models.Evaluation)
                        .filter(models.Evaluation.assignment_id == assignment.id)
                        .all()
                    )

                    # Calculate average score for this employee
                    total_score = 0
                    score_count = 0
                    for eval in evaluations:
                        if (
                            eval.total_score is not None
                            and 0 <= eval.total_score <= 100
                        ):
                            total_score += eval.total_score
                            score_count += 1

                    avg_score = (
                        round(total_score / score_count, 2) if score_count > 0 else None
                    )

                    # Get current level info
                    current_level = (
                        db.query(models.TrainingLevel)
                        .filter(models.TrainingLevel.id == assignment.current_level_id)
                        .first()
                    )

                    enrolled_employees.append(
                        {
                            "employee_id": user.id,
                            "employee_name": f"{user.first_name} {user.last_name}",
                            "employee_code": user.user_code,
                            "department": user.department,
                            "designation": user.designation,
                            "assignment_id": assignment.id,
                            "assignment_status": assignment.status.value,
                            "current_level": (
                                current_level.level_name if current_level else "Unknown"
                            ),
                            "training_start_date": assignment.training_start_date,
                            "training_end_date": assignment.training_end_date,
                            "actual_completion_date": assignment.actual_completion_date,
                            "average_score": avg_score,
                            "last_updated": assignment.updated_at,
                        }
                    )

            # Calculate completion metrics
            completion_rate = round(
                (completed_count / total_enrolled * 100) if total_enrolled > 0 else 0, 2
            )

            progress_rate = round(
                (
                    ((completed_count + in_progress_count) / total_enrolled * 100)
                    if total_enrolled > 0
                    else 0
                ),
                2,
            )

            # Get level information for this training
            levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == training.id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            level_info = []
            for level in levels:
                # Count employees at each level
                level_assignments = (
                    db.query(models.Assignment)
                    .filter(
                        models.Assignment.training_id == training.id,
                        models.Assignment.current_level_id == level.id,
                    )
                    .count()
                )

                level_info.append(
                    {
                        "level_id": level.id,
                        "level_name": level.level_name,
                        "level_order": level.level_order,
                        "employees_at_level": level_assignments,
                        "pass_percentage": level.pass_percentage,
                        "duration_hours": level.duration_hours,
                    }
                )

            # Get department breakdown
            department_stats = (
                db.query(
                    models.User.department,
                    func.count(models.Assignment.id).label("dept_enrolled"),
                )
                .join(models.Assignment, models.Assignment.user_id == models.User.id)
                .filter(
                    models.Assignment.training_id == training.id,
                    models.User.is_active == True,
                    models.User.role == models.UserRole.employee,
                )
                .group_by(models.User.department)
                .all()
            )

            department_breakdown = []
            for dept in department_stats:
                # Count completed assignments for this department
                dept_completed = (
                    db.query(models.Assignment)
                    .join(models.User, models.User.id == models.Assignment.user_id)
                    .filter(
                        models.Assignment.training_id == training.id,
                        models.User.department == dept.department,
                        models.User.is_active == True,
                        models.Assignment.status == models.TrainingStatus.completed,
                    )
                    .count()
                )

                dept_completion_rate = round(
                    (
                        (dept_completed / dept.dept_enrolled * 100)
                        if dept.dept_enrolled > 0
                        else 0
                    ),
                    2,
                )

                department_breakdown.append(
                    {
                        "department": dept.department,
                        "enrolled": dept.dept_enrolled,
                        "completed": dept_completed,
                        "completion_rate": dept_completion_rate,
                    }
                )

            # Calculate overall average score for the training
            all_evaluations = (
                db.query(models.Evaluation)
                .join(
                    models.Assignment,
                    models.Assignment.id == models.Evaluation.assignment_id,
                )
                .filter(models.Assignment.training_id == training.id)
                .all()
            )

            total_training_score = 0
            valid_score_count = 0
            max_score = 0
            min_score = 100

            for eval in all_evaluations:
                if eval.total_score is not None and 0 <= eval.total_score <= 100:
                    total_training_score += eval.total_score
                    valid_score_count += 1
                    max_score = max(max_score, eval.total_score)
                    min_score = min(min_score, eval.total_score)

            avg_training_score = (
                round(total_training_score / valid_score_count, 2)
                if valid_score_count > 0
                else 0
            )
            max_score = round(max_score, 2) if valid_score_count > 0 else 0
            min_score = round(min_score, 2) if valid_score_count > 0 else 0

            result.append(
                {
                    "training_id": training.id,
                    "title": training.title,
                    "category": training.category,
                    "description": training.description,
                    # Enrollment details with actual employee data
                    "enrollment_stats": {
                        "total_enrolled": total_enrolled,
                        "unique_employees": len(enrolled_employees),
                        "assigned": assigned_count,
                        "in_progress": in_progress_count,
                        "completed": completed_count,
                        "failed": failed_count,
                        "enrolled_employees": enrolled_employees,  # This contains the actual employee details
                    },
                    # Completion metrics
                    "completion_metrics": {
                        "completion_rate": completion_rate,
                        "progress_rate": progress_rate,
                        "dropout_rate": round(
                            (
                                (failed_count / total_enrolled * 100)
                                if total_enrolled > 0
                                else 0
                            ),
                            2,
                        ),
                    },
                    # Score statistics
                    "score_stats": {
                        "avg_score": avg_training_score,
                        "max_score": max_score,
                        "min_score": min_score,
                        "score_range": f"{min_score} - {max_score}",
                    },
                    # Training structure
                    "training_structure": {
                        "total_levels": len(levels),
                        "levels": level_info,
                    },
                    # Department breakdown
                    "department_breakdown": department_breakdown,
                    # Performance indicators
                    "performance_indicators": {
                        "popularity_rank": 0,  # Will be updated after sorting
                        "effectiveness_score": round(
                            ((completion_rate * 0.4) + (avg_training_score * 0.6)), 2
                        ),
                    },
                }
            )

        # Sort by popularity (total enrolled) and update ranks
        result.sort(key=lambda x: x["enrollment_stats"]["total_enrolled"], reverse=True)
        for i, training in enumerate(result):
            training["performance_indicators"]["popularity_rank"] = i + 1

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating training details report: {str(e)}",
        )


# Add these endpoints to your main.py file


@app.get("/reports/monthly-completions")
def get_monthly_completions(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Get monthly completion data for line chart"""
    try:
        # Get assignments grouped by month
        monthly_data = (
            db.query(
                func.date_trunc("month", models.Assignment.created_at).label("month"),
                func.count(models.Assignment.id).label("total_assignments"),
                func.sum(
                    case(
                        (
                            models.Assignment.status == models.TrainingStatus.completed,
                            1,
                        ),
                        else_=0,
                    )
                ).label("completed_assignments"),
            )
            .join(models.User, models.User.id == models.Assignment.user_id)
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .group_by(func.date_trunc("month", models.Assignment.created_at))
            .order_by("month")
            .all()
        )

        result = []
        for data in monthly_data:
            result.append(
                {
                    "month": data.month.strftime("%Y-%m"),
                    "total": data.total_assignments or 0,
                    "completed": data.completed_assignments or 0,
                }
            )

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating monthly completions report: {str(e)}",
        )


@app.get("/reports/course-enrollments")
def get_course_enrollments(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Get course enrollment data for column chart"""
    try:
        course_data = (
            db.query(
                models.Training.id.label("course_id"),
                models.Training.title.label("course_title"),
                func.count(models.Assignment.id).label("enrollments"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
            )
            .join(
                models.Assignment, models.Assignment.training_id == models.Training.id
            )
            .join(models.User, models.User.id == models.Assignment.user_id)
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .group_by(models.Training.id, models.Training.title)
            .order_by(func.count(models.Assignment.id).desc())
            .all()
        )  # Limit to top 10 courses by enrollment

        result = []
        for course in course_data:
            result.append(
                {
                    "course_id": course.course_id,
                    "course_title": course.course_title,
                    "enrollments": course.enrollments or 0,
                    "avg_score": round(course.avg_score or 0, 2),
                }
            )

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating course enrollments report: {str(e)}",
        )


@app.get("/reports/course-completion-rates")
def get_course_completion_rates(
    db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)
):
    """Get course completion rates for pie chart"""
    try:
        course_completion_data = (
            db.query(
                models.Training.id.label("course_id"),
                models.Training.title.label("course_title"),
                func.count(models.Assignment.id).label("total_assignments"),
                func.sum(
                    case(
                        (
                            models.Assignment.status == models.TrainingStatus.completed,
                            1,
                        ),
                        else_=0,
                    )
                ).label("completed_assignments"),
            )
            .join(
                models.Assignment, models.Assignment.training_id == models.Training.id
            )
            .join(models.User, models.User.id == models.Assignment.user_id)
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .group_by(models.Training.id, models.Training.title)
            .having(
                func.count(models.Assignment.id)
                > 0  # Only include courses with assignments
            )
            .all()
        )

        result = []
        for course in course_completion_data:
            completion_rate = round(
                (
                    (course.completed_assignments / course.total_assignments * 100)
                    if course.total_assignments > 0
                    else 0
                ),
                2,
            )
            result.append(
                {
                    "course_id": course.course_id,
                    "course_title": course.course_title,
                    "total_assignments": course.total_assignments,
                    "completed_assignments": course.completed_assignments,
                    "completion_rate": completion_rate,
                }
            )

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating course completion rates report: {str(e)}",
        )
@app.put("/assignments/{assignment_id}/manual-score-update")
def manual_score_update(
    assignment_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Admin can manually update:
    - MCQ and Assignment scores
    - Select any level
    - Optionally move to next level
    - Change assignment status
    """
    assignment = db.query(models.Assignment).filter(models.Assignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    level_id = payload.get("training_level_id")
    move_next = payload.get("move_next_level", False)
    mcq_score = payload.get("mcq_score")
    assignment_score = payload.get("assignment_score")
    new_status = payload.get("status")

    if not level_id:
        raise HTTPException(status_code=400, detail="Training level ID is required")

    # Get selected level
    level = db.query(models.TrainingLevel).filter(models.TrainingLevel.id == level_id).first()
    if not level:
        raise HTTPException(status_code=404, detail="Training level not found")

    # --- Calculate total score ---
    total_score = None
    if mcq_score is not None and assignment_score is not None:
        total_score = (float(mcq_score) + float(assignment_score)) / 2
    elif mcq_score is not None:
        total_score = float(mcq_score)
    elif assignment_score is not None:
        total_score = float(assignment_score)

    # --- Always delete old evaluation for this level (force refresh) ---
    db.query(models.Evaluation).filter(
        models.Evaluation.assignment_id == assignment.id,
        models.Evaluation.training_level_id == level.id
    ).delete()

    # --- Create fresh evaluation record ---
    evaluation = models.Evaluation(
        assignment_id=assignment.id,
        training_level_id=level.id,
        evaluated_by=current_user.id,
        mcq_score=mcq_score,
        assignment_score=assignment_score,
        total_score=total_score,
        status=models.EvaluationStatus.passed
        if total_score and total_score >= level.pass_percentage
        else models.EvaluationStatus.failed,
    )
    db.add(evaluation)

    # --- Update assignment if new_status provided ---
    if new_status:
        if new_status not in [status.value for status in models.TrainingStatus]:
            raise HTTPException(status_code=400, detail="Invalid assignment status")
        assignment.status = models.TrainingStatus(new_status)
        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(evaluation)
    db.refresh(assignment)

    # --- Optional: move to next level ---
    moved_next = False
    if move_next:
        moved_next = assignment.progress_to_next_level(db)
        db.commit()

    return {
        "message": "Manual update completed successfully",
        "assignment_status": assignment.status.value,
        "evaluation_status": evaluation.status.value,
        "moved_next_level": moved_next,
    }


@app.get("/assignments/{assignment_id}/training-details")
def get_assignment_training_details(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed training information for an assignment including levels"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Check if the current user owns this assignment or is admin
        if (
            current_user.role != models.UserRole.admin
            and assignment.user_id != current_user.id
        ):
            raise HTTPException(
                status_code=403,
                detail="You can only view your own assignments"
            )

        # Get training with levels
        training = (
            db.query(models.Training)
            .filter(models.Training.id == assignment.training_id)
            .first()
        )

        if not training:
            raise HTTPException(status_code=404, detail="Training not found")

        # Get all levels for this training
        levels = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.training_id == training.id)
            .order_by(models.TrainingLevel.level_order.asc())
            .all()
        )

        # Get current level
        current_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.id == assignment.current_level_id)
            .first()
        )

        # Get evaluations for this assignment
        evaluations = (
            db.query(models.Evaluation)
            .filter(models.Evaluation.assignment_id == assignment_id)
            .order_by(models.Evaluation.evaluation_date.desc())
            .all()
        )

        # Create detailed evaluations history
        evaluations_history = []
        for eval in evaluations:
            evaluations_history.append({
                "id": eval.id,
                "assignment_id": eval.assignment_id,
                "training_level_id": eval.training_level_id,
                "level_name": eval.training_level.level_name if eval.training_level else "Unknown",
                "attempt_number": eval.attempt_number,
                "evaluation_date": eval.evaluation_date,
                "mcq_score": eval.mcq_score,
                "practical_score": eval.practical_score,
                "assignment_score": eval.assignment_score,
                "total_score": eval.total_score,
                "max_possible_score": eval.max_possible_score,
                "percentage_score": eval.percentage_score,
                "is_passing_score": eval.is_passing_score,
                "status": eval.status,
                "comments": eval.comments,
                "evaluated_by": eval.evaluated_by,
                "evaluator_name": eval.evaluator.full_name if eval.evaluator else "Unknown",
                "created_at": eval.created_at,
            })

        # Create level progress information
        level_progress = []
        for level in levels:
            level_eval = next(
                (e for e in evaluations if e.training_level_id == level.id), 
                None
            )
            
            level_status = "pending"
            if level.id == assignment.current_level_id:
                level_status = "current"
            elif level_eval and level_eval.status == models.EvaluationStatus.passed:
                level_status = "completed"
            elif level_eval and level_eval.status == models.EvaluationStatus.failed:
                level_status = "failed"
            elif level.level_order < current_level.level_order:
                level_status = "locked"

            level_progress.append({
                "id": level.id,
                "name": level.level_name,
                "order": level.level_order,
                "description": level.description,
                "duration_hours": level.duration_hours,
                "exam_questions_count": level.exam_questions_count,
                "exam_duration_minutes": level.exam_duration_minutes,
                "pass_percentage": level.pass_percentage,
                "status": level_status,
                "score": level_eval.total_score if level_eval else None,
                "mcq_score": level_eval.mcq_score if level_eval else None,
                "assignment_score": level_eval.assignment_score if level_eval else None,
                "comments": level_eval.comments if level_eval else None,
                "attempts": level_eval.attempt_number if level_eval else 0,
                "evaluation_date": level_eval.evaluation_date if level_eval else None,
            })

        return {
            "assignment_id": assignment.id,
            "training": {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
            },
            "current_level": {
                "id": current_level.id,
                "name": current_level.level_name,
                "order": current_level.level_order,
                "pass_percentage": current_level.pass_percentage,
            },
            "levels": level_progress,
            "evaluations_history": evaluations_history,
            "status": assignment.status,
            "training_start_date": assignment.training_start_date,
            "training_end_date": assignment.training_end_date,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching training details: {str(e)}"
        )


@app.post("/assignments/{assignment_id}/submit-level")
def submit_level_for_evaluation(
    assignment_id: int,
    submission_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Submit a level for evaluation (for employees)"""
    try:
        assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Check if the current user owns this assignment
        if assignment.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="You can only submit your own assignments"
            )

        # Check if assignment is in progress
        if assignment.status not in [models.TrainingStatus.assigned, models.TrainingStatus.in_progress]:
            raise HTTPException(
                status_code=400,
                detail="Cannot submit completed or failed assignment"
            )

        current_level = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.id == assignment.current_level_id)
            .first()
        )

        if not current_level:
            raise HTTPException(status_code=404, detail="Current level not found")

        # Store current level for email
        previous_level = current_level

        # Extract scores from submission
        mcq_score = submission_data.get("mcq_score")
        assignment_score = submission_data.get("assignment_score")

        # Validate scores are provided
        if mcq_score is None and assignment_score is None:
            raise HTTPException(
                status_code=400,
                detail="At least one score (MCQ or Assignment) must be provided"
            )

        # Convert scores to float and handle empty values
        if mcq_score is not None:
            try:
                mcq_score = float(mcq_score)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail="MCQ score must be a valid number"
                )

        if assignment_score is not None:
            try:
                assignment_score = float(assignment_score)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail="Assignment score must be a valid number"
                )

        # Validate score ranges
        for score_name, score_value in [("MCQ", mcq_score), ("Assignment", assignment_score)]:
            if score_value is not None and (score_value < 0 or score_value > 100):
                raise HTTPException(
                    status_code=400,
                    detail=f"{score_name} score must be between 0 and 100"
                )

        # Calculate total score
        total_score = None
        if mcq_score is not None and assignment_score is not None:
            total_score = (mcq_score + assignment_score) / 2
        elif mcq_score is not None:
            total_score = mcq_score
        elif assignment_score is not None:
            total_score = assignment_score

        # Check if level is passed
        is_passed = total_score >= current_level.pass_percentage

        # Get existing evaluation for this level
        existing_eval = (
            db.query(models.Evaluation)
            .filter(
                models.Evaluation.assignment_id == assignment_id,
                models.Evaluation.training_level_id == current_level.id,
            )
            .first()
        )

        # Delete existing evaluation to prevent duplicates
        if existing_eval:
            db.delete(existing_eval)
            db.flush()

        # Create new evaluation
        evaluation = models.Evaluation(
            assignment_id=assignment_id,
            training_level_id=current_level.id,
            attempt_number=1,
            evaluation_date=datetime.utcnow(),
            mcq_score=mcq_score,
            assignment_score=assignment_score,
            total_score=total_score,
            max_possible_score=100.0,
            evaluated_by=current_user.id,  # Self-evaluation by employee
            status=(
                models.EvaluationStatus.passed 
                if is_passed 
                else models.EvaluationStatus.failed
            ),
        )
        db.add(evaluation)

        # Update assignment based on result
        if is_passed:
            # Progress to next level or complete assignment
            training_levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == assignment.training_id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            current_level_index = next(
                (
                    i
                    for i, level in enumerate(training_levels)
                    if level.id == assignment.current_level_id
                ),
                -1,
            )

            if (
                current_level_index >= 0
                and current_level_index < len(training_levels) - 1
            ):
                # Move to next level
                next_level = training_levels[current_level_index + 1]
                assignment.current_level_id = next_level.id
                assignment.status = models.TrainingStatus.assigned
                message = f"Level passed! Progressed to {next_level.level_name}"
                
                # Send level progression email
                try:
                    email_service.send_level_progression_email(
                        employee_email=assignment.user.email,
                        employee_name=assignment.user.full_name,
                        training_title=assignment.training.title,
                        previous_level=previous_level.level_name,
                        new_level=next_level.level_name,
                        new_level_description=next_level.description or "No description available",
                        duration_hours=next_level.duration_hours or 0,
                        prerequisites=next_level.prerequisites or "None",
                        learning_objectives=next_level.learning_objectives or "Not specified"
                    )
                except Exception as email_error:
                    print(f"Failed to send progression email: {str(email_error)}")
            else:
                # This is the final level - mark assignment as completed
                assignment.status = models.TrainingStatus.completed
                assignment.actual_completion_date = datetime.utcnow()
                message = "Final level passed! Assignment completed successfully."
        else:
            # Level failed - stay on current level but mark as failed
            assignment.status = models.TrainingStatus.in_progress
            message = f"Level not passed. Score {total_score:.1f}% is below required {current_level.pass_percentage}%. Please try again."

        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        db.commit()

        return {
            "message": message,
            "assignment_id": assignment_id,
            "level_passed": is_passed,
            "scores": {
                "mcq_score": mcq_score,
                "assignment_score": assignment_score,
                "total_score": total_score,
                "required_score": current_level.pass_percentage,
            },
            "current_level": current_level.level_name,
            "assignment_status": assignment.status,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error submitting level: {str(e)}"
        )

@app.get("/my-assignments/detailed")
def get_my_assignments_detailed(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed assignments for the current user with training and level information"""
    try:
        assignments = (
            db.query(models.Assignment)
            .filter(models.Assignment.user_id == current_user.id)
            .all()
        )

        result = []
        for assignment in assignments:
            # Get training with levels
            training = (
                db.query(models.Training)
                .filter(models.Training.id == assignment.training_id)
                .first()
            )

            # Get current level
            current_level = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.id == assignment.current_level_id)
                .first()
            )

            # Get all levels for progress calculation
            all_levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == assignment.training_id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            # Get evaluations for completed levels
            evaluations = (
                db.query(models.Evaluation)
                .filter(models.Evaluation.assignment_id == assignment.id)
                .all()
            )

            # Calculate progress
            completed_levels = [
                eval for eval in evaluations 
                if eval.status == models.EvaluationStatus.passed
            ]
            progress_percentage = (
                (len(completed_levels) / len(all_levels)) * 100 
                if all_levels 
                else 0
            )

            # Get latest evaluation for current level
            current_level_eval = next(
                (eval for eval in evaluations if eval.training_level_id == current_level.id),
                None
            )

            assignment_data = {
                "id": assignment.id,
                "training_id": assignment.training_id,
                "training_title": training.title if training else "Unknown",
                "training_description": training.description if training else "",
                "training_category": training.category if training else "",
                "current_level_id": assignment.current_level_id,
                "current_level_name": current_level.level_name if current_level else "Unknown",
                "current_level_order": current_level.level_order if current_level else 0,
                "status": assignment.status,
                "training_start_date": assignment.training_start_date,
                "training_end_date": assignment.training_end_date,
                "actual_completion_date": assignment.actual_completion_date,
                "progress_percentage": progress_percentage,
                "total_levels": len(all_levels),
                "completed_levels": len(completed_levels),
                "current_level_score": current_level_eval.total_score if current_level_eval else None,
                "current_level_passed": current_level_eval.status == models.EvaluationStatus.passed if current_level_eval else False,
                "current_level_attempts": current_level_eval.attempt_number if current_level_eval else 0,
                "can_progress": (
                    current_level_eval is not None and 
                    current_level_eval.status == models.EvaluationStatus.passed
                ),
                "levels": [
                    {
                        "id": level.id,
                        "name": level.level_name,
                        "order": level.level_order,
                        "pass_percentage": level.pass_percentage,
                        "exam_questions_count": level.exam_questions_count,
                        "exam_duration_minutes": level.exam_duration_minutes,
                        "is_current": level.id == assignment.current_level_id,
                        "is_completed": any(
                            eval.training_level_id == level.id and 
                            eval.status == models.EvaluationStatus.passed 
                            for eval in evaluations
                        ),
                    }
                    for level in all_levels
                ],
                "evaluations": [
                    {
                        "id": eval.id,
                        "training_level_id": eval.training_level_id,
                        "attempt_number": eval.attempt_number,
                        "total_score": eval.total_score,
                        "status": eval.status,
                        "evaluation_date": eval.evaluation_date,
                    }
                    for eval in evaluations
                ]
            }

            result.append(assignment_data)

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching detailed assignments: {str(e)}"
        )
    
@app.put("/assignments/{assignment_id}/update-status", response_model=schemas.AssignmentOut)
def update_assignment_status(
    assignment_id: int,
    status_update: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Allow employees to update their own assignment status with validation
    """
    try:
        # Find the assignment
        db_assignment = (
            db.query(models.Assignment)
            .filter(models.Assignment.id == assignment_id)
            .first()
        )

        if not db_assignment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found"
            )

        # Check if the current user owns this assignment or is admin
        if (
            current_user.role != models.UserRole.admin
            and db_assignment.user_id != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only update your own assignments",
            )

        # Validate status
        new_status = status_update.get("status")
        if new_status not in [status.value for status in models.TrainingStatus]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {[status.value for status in models.TrainingStatus]}",
            )

        # *** VALIDATION: Prevent marking as completed without passing marks ***
        if new_status == "completed":
            # Get current level requirements
            current_level = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.id == db_assignment.current_level_id)
                .first()
            )
            
            if not current_level:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current training level not found"
                )

            # Get latest evaluation for current level
            latest_evaluation = (
                db.query(models.Evaluation)
                .filter(
                    models.Evaluation.assignment_id == assignment_id,
                    models.Evaluation.training_level_id == db_assignment.current_level_id,
                )
                .order_by(models.Evaluation.evaluation_date.desc())
                .first()
            )

            # Check if employee has passed the current level
            if not latest_evaluation or latest_evaluation.status != models.EvaluationStatus.passed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot mark as completed. Employee has not passed the current level '{current_level.level_name}'. Required passing score: {current_level.pass_percentage}%"
                )

            # For multi-level trainings, check if this is the final level
            training_levels = (
                db.query(models.TrainingLevel)
                .filter(models.TrainingLevel.training_id == db_assignment.training_id)
                .order_by(models.TrainingLevel.level_order.asc())
                .all()
            )

            if training_levels:
                final_level = training_levels[-1]
                if db_assignment.current_level_id != final_level.id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Cannot mark as completed. Employee is on level '{current_level.level_name}' but must complete all {len(training_levels)} levels first."
                    )

        # Update the assignment
        db_assignment.status = models.TrainingStatus(new_status)
        db_assignment.updated_by = current_user.id
        db_assignment.updated_at = datetime.utcnow()

        # If marking as completed and no completion date set, set it
        if new_status == "completed" and not db_assignment.actual_completion_date:
            db_assignment.actual_completion_date = datetime.utcnow()

        db.commit()
        db.refresh(db_assignment)

        # Return updated assignment with related data
        assignment_dict = {
            "id": db_assignment.id,
            "user_id": db_assignment.user_id,
            "training_id": db_assignment.training_id,
            "current_level_id": db_assignment.current_level_id,
            "status": db_assignment.status,
            "training_start_date": db_assignment.training_start_date,
            "training_end_date": db_assignment.training_end_date,
            "actual_completion_date": db_assignment.actual_completion_date,
            "assigned_by": db_assignment.assigned_by,
            "updated_by": db_assignment.updated_by,
            "created_at": db_assignment.created_at,
            "updated_at": db_assignment.updated_at,
            "user_name": db_assignment.user.full_name,
            "training_title": db_assignment.training.title,
            "current_level_name": db_assignment.current_level.level_name,
            "assigner_name": db_assignment.assigner.full_name,
            "updater_name": (
                db_assignment.updater.full_name if db_assignment.updater else None
            ),
        }

        return assignment_dict

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating assignment status: {str(e)}",
        )
    
# Add these endpoints to your main.py

@app.get("/reports/weekly-training-progress")
def get_weekly_training_progress(
    department: Optional[str] = None,
    week_start: Optional[str] = None,  # Format: YYYY-MM-DD
    week_end: Optional[str] = None,    # Format: YYYY-MM-DD
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Get weekly training progress by department and employees
    """
    try:
        # Calculate week dates if not provided (current week)
        if not week_start or not week_end:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())  # Monday
            week_end = week_start + timedelta(days=6)  # Sunday
        else:
            week_start = datetime.strptime(week_start, "%Y-%m-%d").date()
            week_end = datetime.strptime(week_end, "%Y-%m-%d").date()

        # Base query for department performance
        dept_query = (
            db.query(
                models.User.department,
                func.count(func.distinct(models.Assignment.user_id)).label("total_employees"),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_employees"),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.in_progress,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("in_progress_employees"),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.assigned,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("not_started_employees"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=None,
                    )
                ).label("avg_score"),
                # Calculate completion rate based on assignments
                func.avg(
                    case(
                        (
                            models.Assignment.status == models.TrainingStatus.completed,
                            100.0,
                        ),
                        (
                            models.Assignment.status == models.TrainingStatus.in_progress,
                            50.0,
                        ),
                        else_=0.0,
                    )
                ).label("avg_completion_percentage"),
            )
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .outerjoin(
                models.Evaluation,
                models.Evaluation.assignment_id == models.Assignment.id,
            )
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
        )

        # Filter by department if provided
        if department:
            dept_query = dept_query.filter(models.User.department == department)

        dept_results = dept_query.group_by(models.User.department).all()

        # Get employee-level progress details
        employee_query = (
            db.query(
                models.User.id.label("employee_id"),
                models.User.first_name,
                models.User.last_name,
                models.User.department,
                models.Assignment.id.label("assignment_id"),
                models.Training.title.label("training_title"),
                models.Assignment.status,
                models.TrainingLevel.level_name.label("current_level"),
                # Calculate progress metrics
                func.coalesce(func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100,
                            ),
                            models.Evaluation.total_score,
                        ),
                        else_=0,
                    )
                ), 0).label("avg_score"),
                # Estimate completion percentage based on levels completed
                func.coalesce(
                    (
                        func.count(
                            case(
                                (
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Evaluation.id,
                                ),
                                else_=None,
                            )
                        )
                        * 100.0
                        / func.nullif(func.count(models.TrainingLevel.id), 0)
                    ),
                    0
                ).label("completion_percentage"),
                # Count levels completed this week
                func.count(
                    case(
                        (
                            and_(
                                models.Evaluation.status == models.EvaluationStatus.passed,
                                models.Evaluation.evaluation_date >= week_start,
                                models.Evaluation.evaluation_date <= week_end,
                            ),
                            models.Evaluation.id,
                        ),
                        else_=None,
                    )
                ).label("levels_completed_week"),
                # Estimate hours completed (using level duration hours)
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Evaluation.evaluation_date >= week_start,
                                    models.Evaluation.evaluation_date <= week_end,
                                ),
                                models.TrainingLevel.duration_hours,
                            ),
                            else_=0,
                        )
                    ),
                    0
                ).label("hours_completed_week"),
            )
            .select_from(models.User)
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .join(models.Training, models.Training.id == models.Assignment.training_id)
            .join(models.TrainingLevel, models.TrainingLevel.id == models.Assignment.current_level_id)
            .outerjoin(
                models.Evaluation,
                and_(
                    models.Evaluation.assignment_id == models.Assignment.id,
                    models.Evaluation.evaluation_date >= week_start,
                    models.Evaluation.evaluation_date <= week_end,
                ),
            )
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
        )

        if department:
            employee_query = employee_query.filter(models.User.department == department)

        employee_results = (
            employee_query
            .group_by(
                models.User.id,
                models.User.first_name,
                models.User.last_name,
                models.User.department,
                models.Assignment.id,
                models.Training.title,
                models.Assignment.status,
                models.TrainingLevel.level_name,
            )
            .all()
        )

        # Format department results
        departments_data = []
        for dept in dept_results:
            total_employees = dept.total_employees or 0
            completed_employees = dept.completed_employees or 0
            in_progress_employees = dept.in_progress_employees or 0
            not_started_employees = dept.not_started_employees or 0
            
            departments_data.append(
                {
                    "department": dept.department,
                    "week_start_date": week_start,
                    "week_end_date": week_end,
                    "total_employees": total_employees,
                    "active_employees": completed_employees + in_progress_employees,
                    "total_hours_completed": 0,  # Would need actual tracking
                    "avg_completion_percentage": round(dept.avg_completion_percentage or 0, 2),
                    "avg_score": round(dept.avg_score or 0, 2),
                    "levels_completed": 0,  # Would need actual tracking
                    "employees_completed": completed_employees,
                    "employees_in_progress": in_progress_employees,
                    "employees_not_started": not_started_employees,
                }
            )

        # Format employee results
        employees_data = []
        for emp in employee_results:
            employees_data.append(
                {
                    "employee_id": emp.employee_id,
                    "employee_name": f"{emp.first_name} {emp.last_name}",
                    "department": emp.department,
                    "assignment_id": emp.assignment_id,
                    "training_title": emp.training_title,
                    "week_start_date": week_start,
                    "week_end_date": week_end,
                    "hours_completed": round(emp.hours_completed_week or 0, 2),
                    "levels_completed": emp.levels_completed_week or 0,
                    "total_score": round(emp.avg_score or 0, 2),
                    "completion_percentage": round(emp.completion_percentage or 0, 2),
                    "current_level": emp.current_level or "Not Started",
                    "status": emp.status,
                }
            )

        # Calculate summary statistics
        total_departments = len(departments_data)
        total_employees = sum(dept["total_employees"] for dept in departments_data)
        overall_completion_rate = round(
            sum(dept["avg_completion_percentage"] for dept in departments_data) / total_departments 
            if total_departments > 0 else 0, 2
        )
        overall_avg_score = round(
            sum(dept["avg_score"] for dept in departments_data) / total_departments 
            if total_departments > 0 else 0, 2
        )

        summary = {
            "total_departments": total_departments,
            "total_employees": total_employees,
            "overall_completion_rate": overall_completion_rate,
            "overall_avg_score": overall_avg_score,
            "week_start": week_start,
            "week_end": week_end,
            "report_generated_at": datetime.utcnow(),
        }

        return {
            "departments": departments_data,
            "employees": employees_data,
            "summary": summary,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating weekly training progress report: {str(e)}",
        )


@app.get("/reports/department-weekly-progress")
def get_department_weekly_progress(
    weeks: int = 4,  # Last 4 weeks by default
    department: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Get department progress over multiple weeks for trend analysis
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(weeks=weeks)
        
        # Generate weekly date ranges
        weekly_ranges = []
        current_date = start_date
        while current_date <= end_date:
            week_start = current_date - timedelta(days=current_date.weekday())
            week_end = week_start + timedelta(days=6)
            weekly_ranges.append((week_start, week_end))
            current_date = week_end + timedelta(days=1)

        results = []
        
        for week_start, week_end in weekly_ranges:
            # Query for weekly department progress
            weekly_query = (
                db.query(
                    models.User.department,
                    func.count(func.distinct(models.Assignment.user_id)).label("total_employees"),
                    func.avg(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                100.0,
                            ),
                            (
                                models.Assignment.status == models.TrainingStatus.in_progress,
                                50.0,
                            ),
                            else_=0.0,
                        )
                    ).label("avg_completion_rate"),
                    func.avg(
                        case(
                            (
                                and_(
                                    models.Evaluation.total_score >= 0,
                                    models.Evaluation.total_score <= 100,
                                ),
                                models.Evaluation.total_score,
                            ),
                            else_=None,
                        )
                    ).label("avg_score"),
                    func.count(
                        case(
                            (
                                and_(
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Evaluation.evaluation_date >= week_start,
                                    models.Evaluation.evaluation_date <= week_end,
                                ),
                                models.Evaluation.id,
                            ),
                            else_=None,
                        )
                    ).label("levels_completed"),
                )
                .join(models.Assignment, models.Assignment.user_id == models.User.id)
                .outerjoin(
                    models.Evaluation,
                    and_(
                        models.Evaluation.assignment_id == models.Assignment.id,
                        models.Evaluation.evaluation_date >= week_start,
                        models.Evaluation.evaluation_date <= week_end,
                    ),
                )
                .filter(
                    models.User.is_active == True,
                    models.User.role == models.UserRole.employee,
                    models.Assignment.created_at <= week_end,  # Assignments created before week end
                )
            )
            
            if department:
                weekly_query = weekly_query.filter(models.User.department == department)
                
            weekly_data = (
                weekly_query
                .group_by(models.User.department)
                .all()
            )
            
            for dept_data in weekly_data:
                results.append({
                    "department": dept_data.department,
                    "week_start_date": week_start,
                    "week_end_date": week_end,
                    "total_employees": dept_data.total_employees or 0,
                    "completion_rate": round(dept_data.avg_completion_rate or 0, 2),
                    "avg_score": round(dept_data.avg_score or 0, 2),
                    "levels_completed": dept_data.levels_completed or 0,
                    "week_label": f"Week {week_start.strftime('%Y-%m-%d')}"
                })
        
        return results
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating department weekly progress: {str(e)}",
        )


@app.post("/reports/generate-weekly-progress")
def generate_weekly_progress_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """
    Manually generate and store weekly progress records for all employees
    """
    try:
        # Calculate current week
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        # Get all active employees with assignments
        employees_with_assignments = (
            db.query(models.User)
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee,
            )
            .all()
        )
        
        created_count = 0
        
        for user in employees_with_assignments:
            assignments = (
                db.query(models.Assignment)
                .filter(models.Assignment.user_id == user.id)
                .all()
            )
            
            for assignment in assignments:
                # Check if progress record already exists for this week
                existing_record = (
                    db.query(models.TrainingProgress)
                    .filter(
                        models.TrainingProgress.user_id == user.id,
                        models.TrainingProgress.assignment_id == assignment.id,
                        models.TrainingProgress.week_start_date == week_start,
                    )
                    .first()
                )
                
                if existing_record:
                    continue  # Skip if already exists
                
                # Calculate progress metrics for the week
                evaluations_this_week = (
                    db.query(models.Evaluation)
                    .filter(
                        models.Evaluation.assignment_id == assignment.id,
                        models.Evaluation.evaluation_date >= week_start,
                        models.Evaluation.evaluation_date <= week_end,
                    )
                    .all()
                )
                
                # Calculate hours completed (using level duration)
                hours_completed = 0
                levels_completed = 0
                total_score = 0
                valid_scores = 0
                
                for eval in evaluations_this_week:
                    level = (
                        db.query(models.TrainingLevel)
                        .filter(models.TrainingLevel.id == eval.training_level_id)
                        .first()
                    )
                    
                    if level and level.duration_hours:
                        hours_completed += level.duration_hours
                    
                    if eval.status == models.EvaluationStatus.passed:
                        levels_completed += 1
                    
                    if eval.total_score and 0 <= eval.total_score <= 100:
                        total_score += eval.total_score
                        valid_scores += 1
                
                avg_score = total_score / valid_scores if valid_scores > 0 else 0
                
                # Calculate completion percentage
                training_levels = (
                    db.query(models.TrainingLevel)
                    .filter(models.TrainingLevel.training_id == assignment.training_id)
                    .count()
                )
                
                completion_percentage = (
                    (levels_completed / training_levels * 100) 
                    if training_levels > 0 
                    else 0
                )
                
                # Create progress record
                progress_record = models.TrainingProgress(
                    user_id=user.id,
                    assignment_id=assignment.id,
                    training_id=assignment.training_id,
                    department=user.department,
                    week_start_date=week_start,
                    week_end_date=week_end,
                    hours_completed=hours_completed,
                    levels_completed=levels_completed,
                    total_score=avg_score,
                    completion_percentage=completion_percentage,
                    current_level=assignment.current_level.level_name,
                    status=assignment.status,
                )
                
                db.add(progress_record)
                created_count += 1
        
        db.commit()
        
        return {
            "message": f"Successfully generated {created_count} weekly progress records",
            "week_start": week_start,
            "week_end": week_end,
            "records_created": created_count,
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating weekly progress report: {str(e)}",
        )

@app.get("/export/comprehensive-report", response_class=StreamingResponse)
def export_comprehensive_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Export comprehensive training report as a formatted Excel file with charts
    Enhanced to include individual training tables with level-wise breakdowns
    """
    try:
        # Create Excel writer
        output = io.BytesIO()
       
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
           
            # Define formats
            header_format = workbook.add_format({
                'bold': True,
                'font_color': 'white',
                'bg_color': '#2E86AB',
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
           
            title_format = workbook.add_format({
                'bold': True,
                'font_size': 14,
                'font_color': '#2E86AB',
                'align': 'center',
                'valign': 'vcenter'
            })
           
            subheader_format = workbook.add_format({
                'bold': True,
                'font_size': 12,
                'font_color': '#333333',
                'bg_color': '#F8F9FA',
                'border': 1
            })
           
            cell_format = workbook.add_format({
                'border': 1,
                'align': 'left',
                'valign': 'vcenter'
            })
           
            number_format = workbook.add_format({
                'border': 1,
                'align': 'center',
                'valign': 'vcenter'
            })
           
            percentage_format = workbook.add_format({
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
                'num_format': '0.0%'
            })
           
            warning_format = workbook.add_format({
                'border': 1,
                'align': 'left',
                'valign': 'vcenter',
                'bg_color': '#FFEAA7'
            })
           
            success_format = workbook.add_format({
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#D4EDDA'
            })
           
            progress_format = workbook.add_format({
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#FFF3CD'
            })
           
            assigned_format = workbook.add_format({
                'border': 1,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#E2E3E5'
            })

            # Section 1: Executive Summary with Charts
            worksheet1 = workbook.add_worksheet('Executive Summary')
            worksheet1.set_tab_color('#2E86AB')
           
            # Title
            worksheet1.merge_range('A1:F1', 'TRAINING MANAGEMENT SYSTEM - EXECUTIVE SUMMARY', title_format)
            worksheet1.merge_range('A2:F2', f'Generated on: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', cell_format)
            worksheet1.write('A3', '', cell_format)
           
            # Get overview statistics
            total_employees = db.query(models.User).filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee
            ).count()
           
            total_trainings = db.query(models.Training).count()
            total_assignments = db.query(models.Assignment).count()
           
            # Assignment status breakdown
            status_counts = db.query(
                models.Assignment.status,
                func.count(models.Assignment.id).label('count')
            ).group_by(models.Assignment.status).all()
           
            status_dict = {status: count for status, count in status_counts}
            assigned = status_dict.get(models.TrainingStatus.assigned, 0)
            in_progress = status_dict.get(models.TrainingStatus.in_progress, 0)
            completed = status_dict.get(models.TrainingStatus.completed, 0)
            failed = status_dict.get(models.TrainingStatus.failed, 0)
           
            completion_rate = (completed / total_assignments) if total_assignments > 0 else 0
            progress_rate = ((completed + in_progress) / total_assignments) if total_assignments > 0 else 0
           
            # Average score
            avg_score_result = db.query(func.avg(models.Evaluation.total_score)).filter(
                models.Evaluation.total_score >= 0,
                models.Evaluation.total_score <= 100
            ).scalar()
            avg_score = avg_score_result or 0
           
            # Employees with assignments
            employees_with_assignments = db.query(
                func.count(func.distinct(models.Assignment.user_id))
            ).scalar() or 0
           
            engagement_rate = (employees_with_assignments / total_employees) if total_employees > 0 else 0
           
            # Count levels completed
            total_levels_completed = db.query(models.Evaluation).filter(
                models.Evaluation.status == models.EvaluationStatus.passed
            ).count()
           
            # Executive Summary Data
            summary_data = [
                ['Key Metric', 'Value', 'Details'],
                ['Total Employees', total_employees, 'Active employees in system'],
                ['Employees in Training', employees_with_assignments, 'Enrolled in training programs'],
                ['Engagement Rate', engagement_rate, 'Percentage of employees in training'],
                ['Total Training Programs', total_trainings, 'Available training courses'],
                ['Total Training Enrollments', total_assignments, 'Training program enrollments'],
                ['Levels Completed', total_levels_completed, 'Total training levels passed'],
                ['Completion Rate', completion_rate, 'Overall program completion'],
                ['Progress Rate', progress_rate, 'In progress or completed programs'],
                ['Average Score', avg_score/100, 'Average evaluation score across all levels'],
                ['Assigned Programs', assigned, 'Not yet started'],
                ['In Progress Programs', in_progress, 'Currently undergoing training'],
                ['Completed Programs', completed, 'Successfully finished all levels'],
                ['Failed Programs', failed, 'Unsuccessful attempts'],
            ]
           
            # Write summary table
            for row_num, row_data in enumerate(summary_data):
                for col_num, cell_data in enumerate(row_data):
                    if row_num == 0:  # Header row
                        worksheet1.write(row_num + 3, col_num, cell_data, header_format)
                    else:
                        if col_num == 1 and isinstance(cell_data, float):
                            if cell_data <= 1:  # Percentage values
                                worksheet1.write(row_num + 3, col_num, cell_data, percentage_format)
                            else:
                                worksheet1.write(row_num + 3, col_num, cell_data, number_format)
                        else:
                            worksheet1.write(row_num + 3, col_num, cell_data, cell_format)
           
            # Chart 1: Training Status Distribution (Pie Chart)
            status_data = [
                ['Assigned', assigned],
                ['In Progress', in_progress],
                ['Completed', completed],
                ['Failed', failed]
            ]
           
            # Write status data for chart
            worksheet1.write('H4', 'Training Status', header_format)
            worksheet1.write('H5', 'Status', header_format)
            worksheet1.write('I5', 'Count', header_format)
           
            for i, (status, count) in enumerate(status_data):
                worksheet1.write(5 + i, 7, status, cell_format)
                worksheet1.write(5 + i, 8, count, number_format)
           
            # Create pie chart
            chart1 = workbook.add_chart({'type': 'pie'})
            chart1.add_series({
                'name': 'Training Status Distribution',
                'categories': ['Executive Summary', 5, 7, 8, 7],
                'values': ['Executive Summary', 5, 8, 8, 8],
                'data_labels': {'percentage': True, 'category': True}
            })
            chart1.set_title({'name': 'Training Status Distribution'})
            chart1.set_style(10)
            worksheet1.insert_chart('H15', chart1)
           
            # Chart 2: Key Metrics Bar Chart
            metrics_data = [
                ['Employees in Training', employees_with_assignments],
                ['Training Programs', total_trainings],
                ['Training Enrollments', total_assignments],
                ['Levels Completed', total_levels_completed]
            ]
           
            worksheet1.write('L4', 'Key Metrics', header_format)
            worksheet1.write('L5', 'Metric', header_format)
            worksheet1.write('M5', 'Value', header_format)
           
            for i, (metric, value) in enumerate(metrics_data):
                worksheet1.write(5 + i, 11, metric, cell_format)
                worksheet1.write(5 + i, 12, value, number_format)
           
            chart2 = workbook.add_chart({'type': 'column'})
            chart2.add_series({
                'name': 'Key Metrics',
                'categories': ['Executive Summary', 5, 11, 8, 11],
                'values': ['Executive Summary', 5, 12, 8, 12],
                'data_labels': {'value': True}
            })
            chart2.set_title({'name': 'Key Training Metrics'})
            chart2.set_x_axis({'name': 'Metrics'})
            chart2.set_y_axis({'name': 'Count'})
            chart2.set_style(11)
            worksheet1.insert_chart('L15', chart2)
           
            # Adjust column widths
            worksheet1.set_column('A:A', 25)
            worksheet1.set_column('B:B', 15)
            worksheet1.set_column('C:C', 35)
            worksheet1.set_column('H:H', 15)
            worksheet1.set_column('I:I', 10)
            worksheet1.set_column('L:L', 20)
            worksheet1.set_column('M:M', 10)

            # Section 2: Individual Training Breakdowns
            # Get all trainings with detailed level information
            trainings = db.query(models.Training).all()
           
            for training_index, training in enumerate(trainings):
                # Create a worksheet for each training using actual training name
                safe_title = re.sub(r'[\\/*\[\]:?]', '', training.title)[:31]  # Excel sheet name limits
                if not safe_title:
                    safe_title = f"Training_{training.id}"
                
                worksheet = workbook.add_worksheet(safe_title)
                worksheet.set_tab_color('#28a745')
               
                # Title
                worksheet.merge_range('A1:K1', f'TRAINING: {training.title.upper()}', title_format)
                worksheet.merge_range('A2:K2', f'Category: {training.category} | Description: {training.description or "No description"}', cell_format)
                worksheet.write('A3', '', cell_format)
               
                # Get all levels for this training
                levels = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.training_id == training.id
                ).order_by(models.TrainingLevel.level_order.asc()).all()
               
                # Get all assignments for this training
                assignments = db.query(models.Assignment).filter(
                    models.Assignment.training_id == training.id
                ).all()
               
                # Training Overview Headers
                overview_headers = [
                    'Total Employees', 'Completed', 'In Progress', 'Assigned', 'Failed',
                    'Completion Rate', 'Avg Score', 'Total Levels', 'Levels Completed'
                ]
               
                # Calculate training overview metrics
                total_employees_training = len(assignments)
                completed_training = len([a for a in assignments if a.status == models.TrainingStatus.completed])
                in_progress_training = len([a for a in assignments if a.status == models.TrainingStatus.in_progress])
                assigned_training = len([a for a in assignments if a.status == models.TrainingStatus.assigned])
                failed_training = len([a for a in assignments if a.status == models.TrainingStatus.failed])
               
                completion_rate_training = (completed_training / total_employees_training) if total_employees_training > 0 else 0
               
                # Calculate average score for this training
                training_scores = []
                for assignment in assignments:
                    evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id
                    ).all()
                    for eval in evaluations:
                        if eval.total_score and 0 <= eval.total_score <= 100:
                            training_scores.append(eval.total_score)
               
                avg_score_training = round(sum(training_scores) / len(training_scores), 2) if training_scores else 0
               
                # Count levels completed
                levels_completed_training = 0
                for assignment in assignments:
                    passed_evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id,
                        models.Evaluation.status == models.EvaluationStatus.passed
                    ).count()
                    levels_completed_training += passed_evaluations
               
                # Write training overview
                worksheet.write('A4', 'Training Overview', subheader_format)
                for col_num, header in enumerate(overview_headers):
                    worksheet.write(4, col_num, header, header_format)
               
                overview_data = [
                    total_employees_training,
                    completed_training,
                    in_progress_training,
                    assigned_training,
                    failed_training,
                    completion_rate_training,
                    avg_score_training,
                    len(levels),
                    levels_completed_training
                ]
               
                for col_num, data in enumerate(overview_data):
                    if col_num == 5:  # Completion Rate
                        worksheet.write(5, col_num, data, percentage_format)
                    elif col_num == 6:  # Avg Score
                        worksheet.write(5, col_num, data, number_format)
                    else:
                        worksheet.write(5, col_num, data, number_format)
               
                # Level-wise Breakdown Headers
                level_headers = [
                    'Level Name', 'Level Order', 'Employees at Level', 'Completed',
                    'In Progress', 'Assigned', 'Pass Percentage', 'Avg Score',
                    'Completion Rate', 'Status Distribution'
                ]
               
                worksheet.write('A7', 'Level-wise Breakdown', subheader_format)
                for col_num, header in enumerate(level_headers):
                    worksheet.write(7, col_num, header, header_format)
               
                # Level-wise data
                for row_num, level in enumerate(levels):
                    # Count employees at each level and status
                    employees_at_level = len([a for a in assignments if a.current_level_id == level.id])
                   
                    # Get evaluations for this level
                    level_evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.training_level_id == level.id
                    ).all()
                   
                    # Count status for this level
                    completed_level = len([e for e in level_evaluations if e.status == models.EvaluationStatus.passed])
                    in_progress_level = len([a for a in assignments if a.current_level_id == level.id and a.status == models.TrainingStatus.in_progress])
                    assigned_level = len([a for a in assignments if a.current_level_id == level.id and a.status == models.TrainingStatus.assigned])
                   
                    # Calculate average score for this level
                    level_scores = [e.total_score for e in level_evaluations if e.total_score and 0 <= e.total_score <= 100]
                    avg_score_level = round(sum(level_scores) / len(level_scores), 2) if level_scores else 0
                   
                    completion_rate_level = (completed_level / employees_at_level) if employees_at_level > 0 else 0
                   
                    # Status distribution text
                    status_distribution = f"C:{completed_level} | IP:{in_progress_level} | A:{assigned_level}"
                   
                    level_data = [
                        level.level_name,
                        level.level_order,
                        employees_at_level,
                        completed_level,
                        in_progress_level,
                        assigned_level,
                        level.pass_percentage,
                        avg_score_level,
                        completion_rate_level,
                        status_distribution
                    ]
                   
                    for col_num, data in enumerate(level_data):
                        if col_num in [6, 8]:  # Pass Percentage and Completion Rate
                            worksheet.write(8 + row_num, col_num, data/100 if col_num == 6 else data, percentage_format)
                        elif col_num == 7:  # Avg Score
                            worksheet.write(8 + row_num, col_num, data, number_format)
                        elif col_num in [2, 3, 4, 5]:  # Numbers
                            worksheet.write(8 + row_num, col_num, data, number_format)
                        else:
                            worksheet.write(8 + row_num, col_num, data, cell_format)
               
                # Employee Assignment Details
                employee_start_row = 8 + len(levels) + 3
                worksheet.write(employee_start_row, 0, 'Employee Assignments', subheader_format)
               
                employee_headers = [
                    'Employee Name', 'Employee Code', 'Department', 'Current Level',
                    'Assignment Status', 'Start Date', 'End Date', 'Completion Date',
                    'Levels Completed', 'Total Levels', 'Completion %', 'Avg Score'
                ]
               
                for col_num, header in enumerate(employee_headers):
                    worksheet.write(employee_start_row + 1, col_num, header, header_format)
               
                # Employee assignment data
                for emp_row_num, assignment in enumerate(assignments):
                    employee = assignment.user
                   
                    # Get employee's progress in this training
                    training_levels = db.query(models.TrainingLevel).filter(
                        models.TrainingLevel.training_id == training.id
                    ).all()
                   
                    completed_evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id,
                        models.Evaluation.status == models.EvaluationStatus.passed
                    ).count()
                   
                    completion_percentage = (completed_evaluations / len(training_levels)) if training_levels else 0
                   
                    # Calculate average score for this employee in this training
                    employee_evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id
                    ).all()
                    employee_scores = [e.total_score for e in employee_evaluations if e.total_score and 0 <= e.total_score <= 100]
                    avg_score_employee = round(sum(employee_scores) / len(employee_scores), 2) if employee_scores else 0
                   
                    employee_data = [
                        f"{employee.first_name} {employee.last_name}",
                        employee.user_code,
                        employee.department or 'N/A',
                        assignment.current_level.level_name,
                        assignment.status.value,
                        assignment.training_start_date.strftime('%Y-%m-%d') if assignment.training_start_date else 'N/A',
                        assignment.training_end_date.strftime('%Y-%m-%d') if assignment.training_end_date else 'N/A',
                        assignment.actual_completion_date.strftime('%Y-%m-%d') if assignment.actual_completion_date else 'N/A',
                        completed_evaluations,
                        len(training_levels),
                        completion_percentage,
                        avg_score_employee
                    ]
                   
                    for col_num, data in enumerate(employee_data):
                        if col_num == 10:  # Completion Percentage
                            worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, percentage_format)
                        elif col_num == 11:  # Avg Score
                            worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, number_format)
                        elif col_num in [8, 9]:  # Numbers
                            worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, number_format)
                        elif col_num == 4:  # Status with color coding
                            if data == 'completed':
                                worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, success_format)
                            elif data == 'in_progress':
                                worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, progress_format)
                            else:
                                worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, assigned_format)
                        else:
                            worksheet.write(employee_start_row + 2 + emp_row_num, col_num, data, cell_format)
               
                # Adjust column widths for training sheet
                worksheet.set_column('A:A', 20)
                worksheet.set_column('B:B', 15)
                worksheet.set_column('C:C', 15)
                worksheet.set_column('D:D', 20)
                worksheet.set_column('E:E', 15)
                worksheet.set_column('F:F', 12)
                worksheet.set_column('G:G', 12)
                worksheet.set_column('H:H', 15)
                worksheet.set_column('I:I', 15)
                worksheet.set_column('J:J', 15)
                worksheet.set_column('K:K', 12)

            # Section 3: Training Program Performance Comparison
            worksheet3 = workbook.add_worksheet('Training Comparison')
            worksheet3.set_tab_color('#ffc107')
           
            worksheet3.merge_range('A1:L1', 'TRAINING PROGRAM PERFORMANCE COMPARISON', title_format)
            worksheet3.write('A2', '', cell_format)
           
            # Training Comparison Headers
            comparison_headers = [
                'Training Program', 'Category', 'Total Employees', 'Completed',
                'In Progress', 'Assigned', 'Completion Rate', 'Avg Score',
                'Total Levels', 'Levels Completed', 'Level Completion Rate', 'Effectiveness Score'
            ]
           
            for col_num, header in enumerate(comparison_headers):
                worksheet3.write(2, col_num, header, header_format)
           
            # Training comparison data
            for row_num, training in enumerate(trainings):
                assignments = db.query(models.Assignment).filter(
                    models.Assignment.training_id == training.id
                ).all()
               
                total_employees_comp = len(assignments)
                completed_comp = len([a for a in assignments if a.status == models.TrainingStatus.completed])
                in_progress_comp = len([a for a in assignments if a.status == models.TrainingStatus.in_progress])
                assigned_comp = len([a for a in assignments if a.status == models.TrainingStatus.assigned])
               
                completion_rate_comp = (completed_comp / total_employees_comp) if total_employees_comp > 0 else 0
               
                # Calculate average score
                training_scores_comp = []
                for assignment in assignments:
                    evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id
                    ).all()
                    for eval in evaluations:
                        if eval.total_score and 0 <= eval.total_score <= 100:
                            training_scores_comp.append(eval.total_score)
               
                avg_score_comp = round(sum(training_scores_comp) / len(training_scores_comp), 2) if training_scores_comp else 0
               
                # Levels data
                levels_comp = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.training_id == training.id
                ).all()
               
                levels_completed_comp = 0
                for assignment in assignments:
                    passed_evaluations = db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment.id,
                        models.Evaluation.status == models.EvaluationStatus.passed
                    ).count()
                    levels_completed_comp += passed_evaluations
               
                total_possible_levels = len(levels_comp) * total_employees_comp
                level_completion_rate_comp = (levels_completed_comp / total_possible_levels) if total_possible_levels > 0 else 0
               
                # Effectiveness score (weighted combination)
                effectiveness_score = (
                    completion_rate_comp * 0.4 +
                    (avg_score_comp / 100) * 0.3 +
                    level_completion_rate_comp * 0.3
                )
               
                comparison_data = [
                    training.title,
                    training.category,
                    total_employees_comp,
                    completed_comp,
                    in_progress_comp,
                    assigned_comp,
                    completion_rate_comp,
                    avg_score_comp,
                    len(levels_comp),
                    levels_completed_comp,
                    level_completion_rate_comp,
                    effectiveness_score
                ]
               
                for col_num, data in enumerate(comparison_data):
                    if col_num in [6, 10, 11]:  # Rates and percentages
                        worksheet3.write(3 + row_num, col_num, data, percentage_format)
                    elif col_num == 7:  # Avg Score
                        worksheet3.write(3 + row_num, col_num, data, number_format)
                    elif col_num in [2, 3, 4, 5, 8, 9]:  # Numbers
                        worksheet3.write(3 + row_num, col_num, data, number_format)
                    else:
                        worksheet3.write(3 + row_num, col_num, data, cell_format)
           
            worksheet3.set_column('A:A', 30)
            worksheet3.set_column('B:B', 20)
            worksheet3.set_column('C:L', 15)

            # Section 4: Department-wise Training Analysis
            worksheet4 = workbook.add_worksheet('Department Analysis')
            worksheet4.set_tab_color('#6f42c1')
           
            worksheet4.merge_range('A1:H1', 'DEPARTMENT-WISE TRAINING ANALYSIS', title_format)
            worksheet4.write('A2', '', cell_format)
           
            # Get department data
            departments = db.query(
                models.User.department,
                func.count(models.Assignment.id).label('total_assignments'),
                func.count(
                    case(
                        (models.Assignment.status == models.TrainingStatus.completed, models.Assignment.id),
                        else_=None
                    )
                ).label('completed_assignments'),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100
                            ),
                            models.Evaluation.total_score
                        ),
                        else_=None
                    )
                ).label('avg_score')
            ).join(
                models.Assignment, models.Assignment.user_id == models.User.id
            ).outerjoin(
                models.Evaluation, models.Evaluation.assignment_id == models.Assignment.id
            ).filter(
                models.User.department.isnot(None),
                models.User.department != ''
            ).group_by(
                models.User.department
            ).all()
           
            # Department headers
            dept_headers = [
                'Department', 'Total Assignments', 'Completed', 'In Progress',
                'Assigned', 'Completion Rate', 'Avg Score', 'Employee Count'
            ]
           
            for col_num, header in enumerate(dept_headers):
                worksheet4.write(2, col_num, header, header_format)
           
            # Department data
            for row_num, dept in enumerate(departments):
                # Get additional department stats
                dept_employees = db.query(models.User).filter(
                    models.User.department == dept.department,
                    models.User.is_active == True
                ).count()
               
                dept_assignments = db.query(models.Assignment).join(
                    models.User, models.User.id == models.Assignment.user_id
                ).filter(
                    models.User.department == dept.department
                ).all()
               
                in_progress_dept = len([a for a in dept_assignments if a.status == models.TrainingStatus.in_progress])
                assigned_dept = len([a for a in dept_assignments if a.status == models.TrainingStatus.assigned])
               
                completion_rate_dept = (dept.completed_assignments / dept.total_assignments) if dept.total_assignments > 0 else 0
               
                dept_data = [
                    dept.department,
                    dept.total_assignments,
                    dept.completed_assignments,
                    in_progress_dept,
                    assigned_dept,
                    completion_rate_dept,
                    dept.avg_score or 0,
                    dept_employees
                ]
               
                for col_num, data in enumerate(dept_data):
                    if col_num == 5:  # Completion Rate
                        worksheet4.write(3 + row_num, col_num, data, percentage_format)
                    elif col_num == 6:  # Avg Score
                        worksheet4.write(3 + row_num, col_num, data, number_format)
                    elif col_num in [1, 2, 3, 4, 7]:  # Numbers
                        worksheet4.write(3 + row_num, col_num, data, number_format)
                    else:
                        worksheet4.write(3 + row_num, col_num, data, cell_format)
           
            worksheet4.set_column('A:A', 25)
            worksheet4.set_column('B:H', 15)

            # MISSING SHEETS FROM SECOND ENDPOINT - ADDED BELOW

            # Section 5: Training Program Performance with Charts
            worksheet5 = workbook.add_worksheet('Training Programs')
            worksheet5.set_tab_color('#28a745')
           
            # Title
            worksheet5.merge_range('A1:I1', 'TRAINING PROGRAM PERFORMANCE', title_format)
            worksheet5.write('A2', '', cell_format)
           
            # Get training program data
            training_programs_data = db.query(
                models.Training.id,
                models.Training.title,
                models.Training.category,
                models.Training.description,
                func.count(func.distinct(models.Assignment.user_id)).label("enrolled_employees"),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.user_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_employees"),
                func.count(models.Assignment.id).label("total_assignments"),
                func.sum(
                    case(
                        (models.Assignment.status == models.TrainingStatus.completed, 1),
                        else_=0
                    )
                ).label("completed_programs"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0, 
                                models.Evaluation.total_score <= 100
                            ),
                            models.Evaluation.total_score
                        ),
                        else_=None
                    )
                ).label("avg_score"),
                func.count(func.distinct(models.TrainingLevel.id)).label("total_levels"),
                func.count(
                    case(
                        (
                            models.Evaluation.status == models.EvaluationStatus.passed,
                            models.Evaluation.id
                        ),
                        else_=None
                    )
                ).label("levels_completed")
            ).join(
                models.Assignment, models.Assignment.training_id == models.Training.id
            ).join(
                models.TrainingLevel, models.TrainingLevel.training_id == models.Training.id
            ).outerjoin(
                models.Evaluation, 
                and_(
                    models.Evaluation.assignment_id == models.Assignment.id,
                    models.Evaluation.training_level_id == models.TrainingLevel.id
                )
            ).group_by(
                models.Training.id,
                models.Training.title,
                models.Training.category,
                models.Training.description
            ).order_by(
                func.count(func.distinct(models.Assignment.user_id)).desc()
            ).all()
           
            # Training Program Headers
            training_headers = [
                'Training Program', 'Category', 'Description', 'Enrolled Employees',
                'Completed Programs', 'Program Completion Rate', 'Total Levels',
                'Levels Completed', 'Avg Score', 'Effectiveness Score'
            ]
           
            for col_num, header in enumerate(training_headers):
                worksheet5.write(2, col_num, header, header_format)
           
            # Training Program Data
            training_chart_data = []
            for row_num, training in enumerate(training_programs_data):
                program_completion_rate = (
                    (training.completed_programs or 0) / (training.total_assignments or 1)
                )
               
                level_completion_rate = (
                    (training.levels_completed or 0) / 
                    ((training.total_levels or 1) * (training.total_assignments or 1))
                )
               
                avg_score_train = training.avg_score or 0
               
                effectiveness = (
                    (program_completion_rate * 0.4) + 
                    (level_completion_rate * 0.3) + 
                    (avg_score_train/100 * 0.3)
                )
               
                description = training.description or ""
                if len(description) > 100:
                    description = description[:97] + "..."
               
                row_data = [
                    training.title,
                    training.category,
                    description,
                    training.enrolled_employees or 0,
                    training.completed_programs or 0,
                    program_completion_rate,
                    training.total_levels or 0,
                    training.levels_completed or 0,
                    avg_score_train,
                    effectiveness
                ]
               
                # Store data for charts
                training_chart_data.append({
                    'title': training.title,
                    'enrolled': training.enrolled_employees or 0,
                    'completed': training.completed_programs or 0,
                    'completion_rate': program_completion_rate,
                    'avg_score': avg_score_train
                })
               
                for col_num, cell_data in enumerate(row_data):
                    if col_num in [5, 9]:  # Completion Rate and Effectiveness
                        worksheet5.write(row_num + 3, col_num, cell_data, percentage_format)
                    elif col_num == 8:  # Avg Score
                        worksheet5.write(row_num + 3, col_num, cell_data, number_format)
                    elif col_num in [3, 4, 6, 7]:  # Numbers
                        worksheet5.write(row_num + 3, col_num, cell_data, number_format)
                    else:
                        worksheet5.write(row_num + 3, col_num, cell_data, cell_format)
           
            # Chart 3: Top Training Programs by Enrollment
            chart_data_start_row = len(training_programs_data) + 6
            worksheet5.write(chart_data_start_row, 0, 'Program', header_format)
            worksheet5.write(chart_data_start_row, 1, 'Enrolled', header_format)
            worksheet5.write(chart_data_start_row, 2, 'Completed', header_format)
            worksheet5.write(chart_data_start_row, 3, 'Completion Rate', header_format)
           
            # Get top 8 programs for chart
            top_programs = sorted(training_chart_data, key=lambda x: x['enrolled'], reverse=True)[:8]
           
            for i, program in enumerate(top_programs):
                worksheet5.write(chart_data_start_row + 1 + i, 0, program['title'], cell_format)
                worksheet5.write(chart_data_start_row + 1 + i, 1, program['enrolled'], number_format)
                worksheet5.write(chart_data_start_row + 1 + i, 2, program['completed'], number_format)
                worksheet5.write(chart_data_start_row + 1 + i, 3, program['completion_rate'], percentage_format)
           
            chart3 = workbook.add_chart({'type': 'column'})
            chart3.add_series({
                'name': 'Enrolled Employees',
                'categories': ['Training Programs', chart_data_start_row + 1, 0, chart_data_start_row + 8, 0],
                'values': ['Training Programs', chart_data_start_row + 1, 1, chart_data_start_row + 8, 1],
                'data_labels': {'value': True}
            })
            chart3.add_series({
                'name': 'Completed Programs',
                'categories': ['Training Programs', chart_data_start_row + 1, 0, chart_data_start_row + 8, 0],
                'values': ['Training Programs', chart_data_start_row + 1, 2, chart_data_start_row + 8, 2],
                'data_labels': {'value': True}
            })
            chart3.set_title({'name': 'Top Training Programs by Enrollment'})
            chart3.set_x_axis({'name': 'Training Programs'})
            chart3.set_y_axis({'name': 'Number of Employees'})
            chart3.set_style(11)
            worksheet5.insert_chart('F15', chart3)
           
            worksheet5.set_column('A:A', 30)
            worksheet5.set_column('B:B', 20)
            worksheet5.set_column('C:C', 40)
            worksheet5.set_column('D:J', 15)

            # Section 6: Level Performance Breakdown with Charts
            worksheet6 = workbook.add_worksheet('Level Performance')
            worksheet6.set_tab_color('#ffc107')
           
            worksheet6.merge_range('A1:H1', 'TRAINING LEVEL PERFORMANCE BREAKDOWN', title_format)
            worksheet6.write('A2', '', cell_format)
           
            # Get level performance data
            level_performance = db.query(
                models.Training.title.label("training_title"),
                models.TrainingLevel.level_name,
                models.TrainingLevel.level_order,
                models.TrainingLevel.pass_percentage,
                func.count(
                    case(
                        (
                            models.Assignment.current_level_id == models.TrainingLevel.id,
                            models.Assignment.id
                        ),
                        else_=None
                    )
                ).label("employees_at_level"),
                func.count(
                    case(
                        (
                            and_(
                                models.Evaluation.training_level_id == models.TrainingLevel.id,
                                models.Evaluation.status == models.EvaluationStatus.passed
                            ),
                            models.Evaluation.id
                        ),
                        else_=None
                    )
                ).label("employees_passed"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.training_level_id == models.TrainingLevel.id,
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100
                            ),
                            models.Evaluation.total_score
                        ),
                        else_=None
                    )
                ).label("avg_score"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.training_level_id == models.TrainingLevel.id,
                                models.Evaluation.status == models.EvaluationStatus.passed
                            ),
                            100.0
                        ),
                        else_=0.0
                    )
                ).label("pass_rate")
            ).select_from(models.TrainingLevel
            ).join(
                models.Training, models.Training.id == models.TrainingLevel.training_id
            ).outerjoin(
                models.Assignment, models.Assignment.current_level_id == models.TrainingLevel.id
            ).outerjoin(
                models.Evaluation, models.Evaluation.training_level_id == models.TrainingLevel.id
            ).group_by(
                models.Training.title,
                models.TrainingLevel.level_name,
                models.TrainingLevel.level_order,
                models.TrainingLevel.pass_percentage
            ).order_by(
                models.Training.title,
                models.TrainingLevel.level_order
            ).all()
           
            # Level Performance Headers
            level_headers = [
                'Training Program', 'Level', 'Level Order', 'Pass Requirement',
                'Employees at Level', 'Employees Passed', 'Pass Rate', 'Avg Score'
            ]
           
            for col_num, header in enumerate(level_headers):
                worksheet6.write(2, col_num, header, header_format)
           
            # Level Performance Data
            level_chart_data = []
            for row_num, level in enumerate(level_performance):
                pass_rate = (level.pass_rate or 0) / 100
                employees_passed = level.employees_passed or 0
                employees_at_level = level.employees_at_level or 0
               
                row_data = [
                    level.training_title,
                    level.level_name,
                    level.level_order,
                    f"{level.pass_percentage}%",
                    employees_at_level,
                    employees_passed,
                    pass_rate,
                    level.avg_score or 0
                ]
               
                # Store data for charts
                level_chart_data.append({
                    'training': level.training_title,
                    'level': level.level_name,
                    'order': level.level_order,
                    'pass_rate': pass_rate,
                    'avg_score': level.avg_score or 0
                })
               
                for col_num, cell_data in enumerate(row_data):
                    if col_num == 6:  # Pass Rate
                        worksheet6.write(row_num + 3, col_num, cell_data, percentage_format)
                    elif col_num == 7:  # Avg Score
                        worksheet6.write(row_num + 3, col_num, cell_data, number_format)
                    elif col_num in [4, 5]:  # Employee counts
                        worksheet6.write(row_num + 3, col_num, cell_data, number_format)
                    else:
                        worksheet6.write(row_num + 3, col_num, cell_data, cell_format)
           
            worksheet6.set_column('A:A', 30)
            worksheet6.set_column('B:B', 15)
            worksheet6.set_column('C:H', 12)

            # Section 7: Employee Performance with Charts
            worksheet7 = workbook.add_worksheet('Employee Performance')
            worksheet7.set_tab_color('#6f42c1')
           
            worksheet7.merge_range('A1:J1', 'EMPLOYEE TRAINING PERFORMANCE', title_format)
            worksheet7.write('A2', '', cell_format)
           
            # Get employee performance data
            employees = db.query(
                models.User.id,
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
                models.User.department,
                func.count(func.distinct(models.Assignment.training_id)).label("total_programs"),
                func.count(
                    func.distinct(
                        case(
                            (
                                models.Assignment.status == models.TrainingStatus.completed,
                                models.Assignment.training_id,
                            ),
                            else_=None,
                        )
                    )
                ).label("completed_programs"),
                func.count(
                    case(
                        (
                            models.Evaluation.status == models.EvaluationStatus.passed,
                            models.Evaluation.id
                        ),
                        else_=None
                    )
                ).label("levels_completed"),
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100
                            ),
                            models.Evaluation.total_score
                        ),
                        else_=None
                    )
                ).label("avg_score"),
                func.coalesce(
                    (
                        func.count(
                            case(
                                (
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Evaluation.id
                                ),
                                else_=None
                            )
                        ) * 100.0
                        / func.nullif(
                            func.count(models.TrainingLevel.id) * func.count(func.distinct(models.Assignment.training_id)),
                            0
                        )
                    ),
                    0
                ).label("overall_completion_percentage")
            ).join(
                models.Assignment, models.Assignment.user_id == models.User.id
            ).join(
                models.Training, models.Training.id == models.Assignment.training_id
            ).join(
                models.TrainingLevel, models.TrainingLevel.training_id == models.Training.id
            ).outerjoin(
                models.Evaluation, 
                and_(
                    models.Evaluation.assignment_id == models.Assignment.id,
                    models.Evaluation.training_level_id == models.TrainingLevel.id
                )
            ).filter(
                models.User.is_active == True,
                models.User.role == models.UserRole.employee
            ).group_by(
                models.User.id,
                models.User.first_name,
                models.User.last_name,
                models.User.designation,
                models.User.department
            ).order_by(
                func.avg(
                    case(
                        (
                            and_(
                                models.Evaluation.total_score >= 0,
                                models.Evaluation.total_score <= 100
                            ),
                            models.Evaluation.total_score
                        ),
                        else_=None
                    )
                ).desc().nullslast()
            ).all()
           
            # Employee Performance Headers
            emp_headers = [
                'Employee Name', 'Department', 'Designation', 'Training Programs',
                'Completed Programs', 'Levels Completed', 'Overall Completion %',
                'Avg Score', 'Performance Rating'
            ]
           
            for col_num, header in enumerate(emp_headers):
                worksheet7.write(2, col_num, header, header_format)
           
            # Employee Performance Data
            performance_distribution = {'Excellent': 0, 'Very Good': 0, 'Good': 0, 'Satisfactory': 0, 'Needs Improvement': 0}
            low_performers = []
           
            for row_num, emp in enumerate(employees):
                completion_percentage = (emp.overall_completion_percentage or 0) / 100
                avg_score_emp = emp.avg_score or 0
               
                # Performance rating
                if avg_score_emp >= 90 and completion_percentage >= 0.8:
                    rating = "Excellent"
                    performance_distribution['Excellent'] += 1
                    rating_color = cell_format
                elif avg_score_emp >= 80 and completion_percentage >= 0.6:
                    rating = "Very Good"
                    performance_distribution['Very Good'] += 1
                    rating_color = cell_format
                elif avg_score_emp >= 70 and completion_percentage >= 0.5:
                    rating = "Good"
                    performance_distribution['Good'] += 1
                    rating_color = cell_format
                elif avg_score_emp >= 60 and completion_percentage >= 0.4:
                    rating = "Satisfactory"
                    performance_distribution['Satisfactory'] += 1
                    rating_color = cell_format
                else:
                    rating = "Needs Improvement"
                    performance_distribution['Needs Improvement'] += 1
                    rating_color = warning_format
                    low_performers.append({
                        'name': f"{emp.first_name} {emp.last_name}",
                        'department': emp.department,
                        'score': avg_score_emp,
                        'completion_rate': completion_percentage,
                        'programs_completed': emp.completed_programs or 0,
                        'total_programs': emp.total_programs or 0
                    })
               
                row_data = [
                    f"{emp.first_name} {emp.last_name}",
                    emp.department or "N/A",
                    emp.designation or "Employee",
                    emp.total_programs or 0,
                    emp.completed_programs or 0,
                    emp.levels_completed or 0,
                    completion_percentage,
                    avg_score_emp,
                    rating
                ]
               
                for col_num, cell_data in enumerate(row_data):
                    if col_num == 6:  # Completion Percentage
                        worksheet7.write(row_num + 3, col_num, cell_data, percentage_format)
                    elif col_num == 7:  # Avg Score
                        worksheet7.write(row_num + 3, col_num, cell_data, number_format)
                    elif col_num == 8:  # Performance Rating
                        worksheet7.write(row_num + 3, col_num, cell_data, rating_color)
                    elif col_num in [3, 4, 5]:  # Numbers
                        worksheet7.write(row_num + 3, col_num, cell_data, number_format)
                    else:
                        worksheet7.write(row_num + 3, col_num, cell_data, cell_format)
           
            worksheet7.set_column('A:A', 25)
            worksheet7.set_column('B:C', 20)
            worksheet7.set_column('D:I', 15)

            # Section 8: Employees Needing Improvement
            if low_performers:
                worksheet8 = workbook.add_worksheet('Needs Improvement')
                worksheet8.set_tab_color('#dc3545')
               
                worksheet8.merge_range('A1:F1', 'EMPLOYEES NEEDING IMPROVEMENT', title_format)
                worksheet8.write('A2', 'The following employees have performance issues and require additional support:', cell_format)
                worksheet8.write('A3', '', cell_format)
               
                # Headers for improvement list
                improvement_headers = [
                    'Employee Name', 'Department', 'Avg Score', 
                    'Programs Completed', 'Total Programs', 'Completion Rate', 'Action Required'
                ]
                for col_num, header in enumerate(improvement_headers):
                    worksheet8.write(3, col_num, header, header_format)
               
                # Improvement data
                for row_num, emp in enumerate(low_performers):
                    if emp['completion_rate'] < 0.3:
                        action = "High priority - Needs immediate mentoring and support"
                    elif emp['completion_rate'] < 0.5:
                        action = "Medium priority - Regular monitoring and guidance needed"
                    else:
                        action = "Low priority - Encourage completion of remaining programs"
                   
                    row_data = [
                        emp['name'],
                        emp['department'],
                        emp['score'],
                        emp['programs_completed'],
                        emp['total_programs'],
                        emp['completion_rate'],
                        action
                    ]
                   
                    for col_num, cell_data in enumerate(row_data):
                        if col_num == 2:  # Avg Score
                            worksheet8.write(row_num + 4, col_num, cell_data, number_format)
                        elif col_num in [3, 4]:  # Numbers
                            worksheet8.write(row_num + 4, col_num, cell_data, number_format)
                        elif col_num == 5:  # Completion Rate
                            worksheet8.write(row_num + 4, col_num, cell_data, percentage_format)
                        else:
                            worksheet8.write(row_num + 4, col_num, cell_data, warning_format)
               
                worksheet8.set_column('A:A', 25)
                worksheet8.set_column('B:B', 20)
                worksheet8.set_column('C:F', 15)
                worksheet8.set_column('G:G', 40)
       
        # Prepare response
        output.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"comprehensive_training_report_{timestamp}.xlsx"
       
        return StreamingResponse(
            io.BytesIO(output.getvalue()),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            }
        )
       
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating comprehensive report: {str(e)}"
        )
@app.post("/debug-email")
def debug_email_config(
    test_email: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Debug email configuration and test sending
    """
    try:
        # Test SMTP connection
        import smtplib
        
        debug_info = {
            "smtp_server": email_service.smtp_server,
            "smtp_port": email_service.smtp_port,
            "sender_email": email_service.sender_email,
            "use_tls": email_service.use_tls,
            "test_recipient": test_email
        }
        
        # Test 1: SMTP Connection
        try:
            server = smtplib.SMTP(email_service.smtp_server, email_service.smtp_port)
            server.set_debuglevel(1)
            debug_info["smtp_connection"] = "SUCCESS"
            
            # Test 2: TLS
            if email_service.use_tls:
                server.starttls()
                debug_info["tls_handshake"] = "SUCCESS"
            
            # Test 3: Authentication
            server.login(email_service.sender_email, email_service.sender_password)
            debug_info["authentication"] = "SUCCESS"
            
            server.quit()
            
        except Exception as e:
            debug_info["smtp_error"] = str(e)
            return {
                "status": "smtp_failed",
                "debug_info": debug_info,
                "error": f"SMTP configuration error: {str(e)}"
            }
        
        # Test 4: Send actual email
        test_subject = "Test Email - Aligned Automation System"
        test_content = f"""
        <html>
        <body>
            <h2>Test Email from Aligned Automation</h2>
            <p>This is a test email sent on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>If you received this, your email configuration is working correctly.</p>
        </body>
        </html>
        """
        
        email_sent = email_service.send_email(test_email, test_subject, test_content)
        
        debug_info["email_sent"] = email_sent
        
        return {
            "status": "success" if email_sent else "failed",
            "debug_info": debug_info,
            "message": "Test email sent successfully" if email_sent else "Failed to send test email"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
# Add these endpoints to your main.py

# ---------------- MCQ Questions Endpoints (Admin Only) ----------------
@app.post("/mcq-questions", response_model=schemas.MCQQuestionOut)
def create_mcq_question(
    question: schemas.MCQQuestionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Create a new MCQ question for a training level"""
    try:
        # Verify training level exists
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == question.training_level_id
        ).first()
        
        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")

        # Validate correct option
        if question.correct_option.upper() not in ['A', 'B', 'C', 'D']:
            raise HTTPException(status_code=400, detail="Correct option must be A, B, C, or D")

        # Create question
        db_question = models.MCQQuestion(
            **question.dict(),
            created_by=current_user.id
        )
        
        db.add(db_question)
        db.commit()
        db.refresh(db_question)

        # Add creator name
        db_question.creator_name = current_user.full_name

        return db_question

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating MCQ question: {str(e)}")

@app.get("/mcq-questions", response_model=List[schemas.MCQQuestionOut])
def get_mcq_questions(
    training_level_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    created_after: Optional[str] = None,  # New parameter for date filtering
    skip: int = 0,
    # limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get MCQ questions with enhanced filtering"""
    query = db.query(models.MCQQuestion)
    
    if training_level_id:
        query = query.filter(models.MCQQuestion.training_level_id == training_level_id)
    
    if is_active is not None:
        query = query.filter(models.MCQQuestion.is_active == is_active)

    # Add date filtering
    if created_after:
        try:
            created_date = datetime.strptime(created_after, "%Y-%m-%d")
            # Adjust for timezone differences (subtract 6 hours to catch start of day in eastern timezones like IST)
            # 00:00 IST is approx 18:30 UTC previous day
            adjusted_date = created_date - timedelta(hours=6)
            query = query.filter(models.MCQQuestion.created_at >= adjusted_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    questions = query.order_by(models.MCQQuestion.created_at.desc()).offset(skip).all()

    # Add creator names
    for question in questions:
        question.creator_name = question.creator.full_name

    return questions
# ---------------- Enhanced MCQ Questions Endpoints ----------------

@app.post("/mcq-questions/bulk", response_model=List[schemas.MCQQuestionOut])
def create_bulk_mcq_questions(
    questions_data: List[schemas.MCQQuestionCreate],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Create multiple MCQ questions at once"""
    try:
        created_questions = []
        
        for question_data in questions_data:
            # Verify training level exists
            training_level = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.id == question_data.training_level_id
            ).first()
            
            if not training_level:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Training level with ID {question_data.training_level_id} not found"
                )

            # Validate correct option
            if question_data.correct_option.upper() not in ['A', 'B', 'C', 'D']:
                raise HTTPException(
                    status_code=400, 
                    detail="Correct option must be A, B, C, or D"
                )

            # Create question
            db_question = models.MCQQuestion(
                **question_data.dict(),
                created_by=current_user.id
            )
            
            db.add(db_question)
            created_questions.append(db_question)

        db.commit()
        
        # Refresh all created questions to get their IDs
        for question in created_questions:
            db.refresh(question)
            question.creator_name = current_user.full_name

        return created_questions

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Error creating bulk questions: {str(e)}"
        )

@app.post("/mcq-questions/upload-csv")
async def upload_mcq_questions_csv(
    training_level_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Upload MCQ questions via CSV or Excel file"""
    try:
        # Verify training level exists
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == training_level_id
        ).first()

        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")

        # Check file type
        if not file.filename.endswith(('.csv', '.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="File must be CSV or Excel format")

        # ✅ async file read (works now)
        content = await file.read()

        rows = []
        if file.filename.endswith('.csv'):
            import csv
            from io import StringIO
            csv_content = content.decode('utf-8')
            csv_reader = csv.DictReader(StringIO(csv_content))
            rows = list(csv_reader)
        else:
            import openpyxl
            from io import BytesIO
            wb = openpyxl.load_workbook(BytesIO(content))
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))

        if not rows:
            raise HTTPException(status_code=400, detail="CSV file is empty")

        created_count = 0
        error_count = 0
        errors = []

        # Skip header row (assuming it exists)
        headers = [h.lower().strip() for h in rows[0]]
        for i, row_values in enumerate(rows[1:], start=2):
            try:
                row = dict(zip(headers, row_values))
                required_fields = ['question_text', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_option']
                for field in required_fields:
                    if not str(row.get(field, '')).strip():
                        errors.append(f"Row {i}: Missing required field '{field}'")
                        error_count += 1
                        break
                else:
                    correct_option = str(row['correct_option']).upper().strip()
                    if correct_option not in ['A', 'B', 'C', 'D']:
                        errors.append(f"Row {i}: Invalid correct option '{correct_option}'")
                        error_count += 1
                        continue

                    db_question = models.MCQQuestion(
                        training_level_id=training_level_id,
                        question_text=row['question_text'].strip(),
                        option_a=row['option_a'].strip(),
                        option_b=row['option_b'].strip(),
                        option_c=row['option_c'].strip(),
                        option_d=row['option_d'].strip(),
                        correct_option=correct_option,
                        explanation=row.get('explanation', '').strip(),
                        marks=int(row['marks']) if row.get('marks') else 1,
                        created_by=current_user.id,
                    )
                    db.add(db_question)
                    created_count += 1

            except Exception as e:
                errors.append(f"Row {i}: {str(e)}")
                error_count += 1
                continue

        db.commit()

        return {
            "message": f"Upload complete — {created_count} questions added, {error_count} errors",
            "created_count": created_count,
            "error_count": error_count,
            "errors": errors[:10],
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")


@app.put("/mcq-questions/{question_id}/toggle")
def toggle_mcq_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Toggle question active status"""
    try:
        question = db.query(models.MCQQuestion).filter(models.MCQQuestion.id == question_id).first()
        
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")

        question.is_active = not question.is_active
        question.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(question)

        return {
            "message": f"Question {'activated' if question.is_active else 'deactivated'} successfully",
            "question_id": question.id,
            "is_active": question.is_active
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error toggling question: {str(e)}")

@app.post("/mcq-questions/bulk-toggle", response_model=BulkToggleResponse)
def bulk_toggle_mcq_questions(
    toggle_data: MCQQuestionBulkToggle,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Bulk toggle questions active status"""
    try:
        print(f"Bulk toggle received: {toggle_data.question_ids}, is_active: {toggle_data.is_active}")
        
        # Verify all questions exist
        questions = db.query(models.MCQQuestion).filter(
            models.MCQQuestion.id.in_(toggle_data.question_ids)
        ).all()

        print(f"Found {len(questions)} questions to update")

        if not questions:
            raise HTTPException(status_code=404, detail="No questions found")

        updated_count = 0
        for question in questions:
            question.is_active = toggle_data.is_active
            question.updated_at = datetime.utcnow()
            updated_count += 1

        db.commit()
        print(f"Successfully updated {updated_count} questions")

        return {
            "message": f"Successfully {'activated' if toggle_data.is_active else 'deactivated'} {updated_count} questions",
            "updated_count": updated_count
        }

    except Exception as e:
        db.rollback()
        print(f"Bulk toggle error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error bulk toggling questions: {str(e)}")

@app.delete("/mcq-questions/bulk", response_model=BulkDeleteResponse)
def bulk_delete_mcq_questions(
    delete_data: MCQQuestionBulkDelete,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Bulk delete questions"""
    try:
        print(f"Bulk delete received: {delete_data.question_ids}")
        
        questions = db.query(models.MCQQuestion).filter(
            models.MCQQuestion.id.in_(delete_data.question_ids)
        ).all()

        print(f"Found {len(questions)} questions to delete")

        if not questions:
            raise HTTPException(status_code=404, detail="No questions found")

        deleted_count = 0
        for question in questions:
            db.delete(question)
            deleted_count += 1

        db.commit()
        print(f"Successfully deleted {deleted_count} questions")

        return {
            "message": f"Successfully deleted {deleted_count} questions",
            "deleted_count": deleted_count
        }

    except Exception as e:
        db.rollback()
        print(f"Bulk delete error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error bulk deleting questions: {str(e)}")
    
@app.put("/mcq-questions/{question_id}", response_model=schemas.MCQQuestionOut)
def update_mcq_question(
    question_id: int,
    question_update: schemas.MCQQuestionUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Update an MCQ question"""
    db_question = db.query(models.MCQQuestion).filter(models.MCQQuestion.id == question_id).first()
    
    if not db_question:
        raise HTTPException(status_code=404, detail="MCQ question not found")

    update_data = question_update.dict(exclude_unset=True)
    
    # Validate correct option if provided
    if 'correct_option' in update_data and update_data['correct_option'].upper() not in ['A', 'B', 'C', 'D']:
        raise HTTPException(status_code=400, detail="Correct option must be A, B, C, or D")

    for field, value in update_data.items():
        setattr(db_question, field, value)

    db_question.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_question)

    db_question.creator_name = db_question.creator.full_name
    return db_question

@app.delete("/mcq-questions/{question_id}", status_code=204)
def delete_mcq_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Delete an MCQ question (soft delete)"""
    question = db.query(models.MCQQuestion).filter(models.MCQQuestion.id == question_id).first()
    
    if not question:
        raise HTTPException(status_code=404, detail="MCQ question not found")

    question.is_active = False
    db.commit()

# ---------------- MCQ Exam Endpoints ----------------
@app.post("/mcq-exam/start")
def start_mcq_exam(
    exam_data: schemas.MCQExamStart,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Start a new MCQ exam for a training level - WITH DYNAMIC SETTINGS"""
    try:
        # Verify assignment exists and belongs to user
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == exam_data.assignment_id
        ).first()
        
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")
        
        if current_user.role != models.UserRole.admin and assignment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized for this assignment")

        # Verify training level matches assignment current level
        if assignment.current_level_id != exam_data.training_level_id:
            raise HTTPException(
                status_code=400, 
                detail="Training level does not match assignment current level"
            )

        # Check if assignment is already failed
        if assignment.status == models.TrainingStatus.failed:
            raise HTTPException(
                status_code=400,
                detail="This assignment has failed. Please contact administrator to reset."
            )

        # Check exam attempts before starting
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == exam_data.training_level_id
        ).first()

        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")

        # PRIORITIZE DB SETTINGS FOR EMPLOYEES to avoid stale frontend data causing errors
        if current_user.role == models.UserRole.admin and exam_data.number_of_questions:
             number_of_questions = exam_data.number_of_questions
        else:
             number_of_questions = training_level.exam_questions_count

        if current_user.role == models.UserRole.admin and exam_data.duration_minutes:
             exam_duration_minutes = exam_data.duration_minutes
        else:
             exam_duration_minutes = training_level.exam_duration_minutes

        # Count failed attempts for this level
        failed_attempts = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == exam_data.assignment_id,
            models.MCQExamAttempt.training_level_id == exam_data.training_level_id,
            models.MCQExamAttempt.status == 'failed'
        ).count()

        max_attempts = training_level.max_attempts
        
        # Check if user has any remaining attempts
        if failed_attempts >= max_attempts:
            if assignment.status != models.TrainingStatus.failed:
                assignment.status = models.TrainingStatus.failed
                assignment.updated_at = datetime.utcnow()
                db.commit()
            
            raise HTTPException(
                status_code=400,
                detail=f"You have failed all {max_attempts} attempts. Please contact administrator."
            )

        # Check if user has already passed this level
        passed_attempt = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == exam_data.assignment_id,
            models.MCQExamAttempt.training_level_id == exam_data.training_level_id,
            models.MCQExamAttempt.status == 'passed'
        ).first()

        if passed_attempt:
            raise HTTPException(
                status_code=400,
                detail="You have already passed this level's exam."
            )

        # Check for any in-progress exam attempts for this level
        in_progress_attempt = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == exam_data.assignment_id,
            models.MCQExamAttempt.training_level_id == exam_data.training_level_id,
            models.MCQExamAttempt.status == 'in_progress'
        ).first()

        if in_progress_attempt:
            # Return existing in-progress attempt
            questions = db.query(models.MCQQuestion).filter(
                models.MCQQuestion.id.in_(
                    db.query(models.MCQAnswer.question_id).filter(
                        models.MCQAnswer.exam_attempt_id == in_progress_attempt.id
                    )
                )
            ).all()

            exam_questions = []
            for q in questions:
                exam_questions.append({
                    "id": q.id,
                    "question_text": q.question_text,
                    "options": {
                        "A": q.option_a,
                        "B": q.option_b,
                        "C": q.option_c,
                        "D": q.option_d
                    },
                    "marks": q.marks,
                    "question_image": q.question_image,
                    "option_images": {
                        "A": q.option_a_image,
                        "B": q.option_b_image,
                        "C": q.option_c_image,
                        "D": q.option_d_image
                    }
                })

            return {
                "exam_attempt_id": in_progress_attempt.id,
                "questions": exam_questions,
                "total_questions": in_progress_attempt.total_questions,
                "training_level_id": exam_data.training_level_id, 
                "assignment_id": exam_data.assignment_id,
                "attempt_number": in_progress_attempt.attempt_number,
                "remaining_attempts": max_attempts - failed_attempts,
                "started_at": in_progress_attempt.started_at,
                "resumed_exam": True,
                "exam_settings": {
                    "total_questions": in_progress_attempt.total_questions,
                    "duration_minutes": in_progress_attempt.duration_minutes,
                    "passing_percentage": training_level.pass_percentage
                }
            }

        # Get active questions for this SPECIFIC level only
        questions = db.query(models.MCQQuestion).filter(
            models.MCQQuestion.training_level_id == exam_data.training_level_id,
            models.MCQQuestion.is_active == True
        ).all()

        # Use the dynamic number of questions from request
        if len(questions) < number_of_questions:
            raise HTTPException(
                status_code=400, 
                detail=f"Not enough questions available for this level. Available: {len(questions)}, Required: {number_of_questions}"
            )

        # Get questions that user hasn't attempted before in previous exams
        previous_attempts = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == exam_data.assignment_id,
            models.MCQExamAttempt.training_level_id == exam_data.training_level_id
        ).all()

        previous_question_ids = set()
        for attempt in previous_attempts:
            answers = db.query(models.MCQAnswer).filter(
                models.MCQAnswer.exam_attempt_id == attempt.id
            ).all()
            previous_question_ids.update([answer.question_id for answer in answers])

        # Filter out questions that user has seen in previous attempts
        available_questions = [q for q in questions if q.id not in previous_question_ids]
        
        # If not enough new questions, include some from previous attempts
        if len(available_questions) < number_of_questions:
            needed = number_of_questions - len(available_questions)
            previous_questions = [q for q in questions if q.id in previous_question_ids]
            import random
            if previous_questions:
                additional_questions = random.sample(previous_questions, min(needed, len(previous_questions)))
                available_questions.extend(additional_questions)
            else:
                available_questions = questions

        # Select random questions
        import random
        if len(available_questions) > number_of_questions:
            selected_questions = random.sample(available_questions, number_of_questions)
        else:
            selected_questions = available_questions

        # Calculate attempt number
        total_attempts = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == exam_data.assignment_id,
            models.MCQExamAttempt.training_level_id == exam_data.training_level_id
        ).count()

        attempt_number = total_attempts + 1

        # Create exam attempt
        exam_attempt = models.MCQExamAttempt(
            assignment_id=exam_data.assignment_id,
            training_level_id=exam_data.training_level_id,
            total_questions=number_of_questions,
            attempt_number=attempt_number,
            duration_minutes=exam_duration_minutes
        )

        db.add(exam_attempt)
        db.commit()
        db.refresh(exam_attempt)

        # Store the selected questions in answers table
        for question in selected_questions:
            answer = models.MCQAnswer(
                exam_attempt_id=exam_attempt.id,
                question_id=question.id,
                selected_option=None,
                is_correct=False,
                marks_obtained=0,
                time_taken_seconds=0
            )
            db.add(answer)
        
        db.commit()

        # Prepare questions for exam
        exam_questions = []
        for q in selected_questions:
            exam_questions.append({
                "id": q.id,
                "question_text": q.question_text,
                "options": {
                    "A": q.option_a,
                    "B": q.option_b,
                    "C": q.option_c,
                    "D": q.option_d
                },
                "marks": q.marks,
                "question_image": q.question_image,
                "option_images": {
                    "A": q.option_a_image,
                    "B": q.option_b_image,
                    "C": q.option_c_image,
                    "D": q.option_d_image
                }
            })

        return {
            "exam_attempt_id": exam_attempt.id,
            "questions": exam_questions,
            "total_questions": number_of_questions,
            "training_level_id": exam_data.training_level_id, 
            "assignment_id": exam_data.assignment_id,
            "attempt_number": attempt_number,
            "remaining_attempts": max_attempts - failed_attempts,
            "started_at": exam_attempt.started_at,
            "resumed_exam": False,
            "exam_settings": {
                "total_questions": number_of_questions,
                "duration_minutes": exam_duration_minutes,  # Return dynamic duration in response
                "passing_percentage": training_level.pass_percentage
            }
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error starting MCQ exam: {str(e)}")
@app.post("/mcq-exam/{exam_attempt_id}/submit")
def submit_mcq_exam(
    exam_attempt_id: int,
    submission: schemas.MCQExamSubmit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Submit MCQ exam answers and calculate results with level progression logic - FIXED VERSION"""
    try:
        # Get exam attempt
        exam_attempt = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.id == exam_attempt_id
        ).first()

        if not exam_attempt:
            raise HTTPException(status_code=404, detail="Exam attempt not found")

        # Verify ownership
        if current_user.role != models.UserRole.admin and exam_attempt.assignment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized for this exam")

        # Check if exam was already submitted (including auto-submitted)
        if exam_attempt.status != 'in_progress':
            return {
                "exam_attempt_id": exam_attempt_id,
                "status": exam_attempt.status,
                "message": f"Exam already submitted with status: {exam_attempt.status}",
                "percentage_score": exam_attempt.percentage_score or 0,
                "assignment_status": exam_attempt.assignment.status if exam_attempt.assignment else "unknown"
            }

        # Get training level requirements
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == exam_attempt.training_level_id
        ).first()

        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")

        # Calculate results
        total_marks = 0
        correct_answers = 0
        details = []

        for answer_data in submission.answers:
            question = db.query(models.MCQQuestion).filter(
                models.MCQQuestion.id == answer_data.question_id
            ).first()

            if not question:
                continue

            is_correct = (answer_data.selected_option.upper() == question.correct_option.upper())
            marks_obtained = question.marks if is_correct else 0

            if is_correct:
                correct_answers += 1
                total_marks += marks_obtained

            # Store answer
            answer = models.MCQAnswer(
                exam_attempt_id=exam_attempt_id,
                question_id=answer_data.question_id,
                selected_option=answer_data.selected_option.upper(),
                is_correct=is_correct,
                marks_obtained=marks_obtained,
                time_taken_seconds=answer_data.time_taken_seconds
            )
            db.add(answer)

            # Add to details for response
            details.append({
                "question_id": question.id,
                "question_text": question.question_text,
                "options": {
                    "A": question.option_a,
                    "B": question.option_b,
                    "C": question.option_c,
                    "D": question.option_d
                },
                "correct_option": question.correct_option,
                "selected_option": answer_data.selected_option.upper(),
                "is_correct": is_correct,
                "explanation": question.explanation,
                "marks_obtained": marks_obtained,
                "question_image": question.question_image,
                "option_images": {
                    "A": question.option_a_image,
                    "B": question.option_b_image,
                    "C": question.option_c_image,
                    "D": question.option_d_image
                }
            })

        # Update exam attempt
        exam_attempt.questions_attempted = len(submission.answers)
        exam_attempt.correct_answers = correct_answers
        exam_attempt.total_marks = total_marks
        exam_attempt.percentage_score = (correct_answers / exam_attempt.total_questions) * 100 if exam_attempt.total_questions > 0 else 0
        exam_attempt.time_taken_seconds = submission.total_time_taken_seconds
        exam_attempt.completed_at = datetime.utcnow()

        passing_percentage = training_level.pass_percentage
        assignment = exam_attempt.assignment

        # Count previous failed attempts for this level (excluding current attempt)
        previous_failed_attempts = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == assignment.id,
            models.MCQExamAttempt.training_level_id == training_level.id,
            models.MCQExamAttempt.status == 'failed',
            models.MCQExamAttempt.id != exam_attempt.id
        ).count()

        max_attempts = training_level.max_attempts

        # Determine exam status
        if exam_attempt.percentage_score >= passing_percentage:
            exam_attempt.status = 'passed'
            
            # Level progression logic
            training_levels = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.training_id == assignment.training_id
            ).order_by(models.TrainingLevel.level_order.asc()).all()

            if not training_levels:
                raise HTTPException(status_code=404, detail="No training levels found")

            current_level_index = next(
                (i for i, level in enumerate(training_levels) 
                 if level.id == assignment.current_level_id),
                -1
            )

            if current_level_index == -1:
                raise HTTPException(status_code=404, detail="Current level not found")

            if current_level_index < len(training_levels) - 1:
                next_level = training_levels[current_level_index + 1]
                assignment.current_level_id = next_level.id
                assignment.status = models.TrainingStatus.in_progress
                
                evaluation = models.Evaluation(
                    assignment_id=assignment.id,
                    training_level_id=training_level.id,
                    attempt_number=exam_attempt.attempt_number,
                    evaluation_date=datetime.utcnow(),
                    mcq_score=exam_attempt.percentage_score,
                    total_score=exam_attempt.percentage_score,
                    max_possible_score=100.0,
                    evaluated_by=current_user.id,
                    status=models.EvaluationStatus.passed
                )
                db.add(evaluation)
                
                message = f"Exam passed! Progressed to {next_level.level_name} level"
                
            else:
                assignment.status = models.TrainingStatus.completed
                assignment.actual_completion_date = datetime.utcnow()
                
                evaluation = models.Evaluation(
                    assignment_id=assignment.id,
                    training_level_id=training_level.id,
                    attempt_number=exam_attempt.attempt_number,
                    evaluation_date=datetime.utcnow(),
                    mcq_score=exam_attempt.percentage_score,
                    total_score=exam_attempt.percentage_score,
                    max_possible_score=100.0,
                    evaluated_by=current_user.id,
                    status=models.EvaluationStatus.passed
                )
                db.add(evaluation)
                
                message = "Final level completed! Training completed successfully."

        else:
            exam_attempt.status = 'failed'
            
            total_failed_attempts_now = previous_failed_attempts + 1
            
            if total_failed_attempts_now >= max_attempts:
                assignment.status = models.TrainingStatus.failed
                message = f"Exam failed! You have used all {max_attempts} attempts. Assignment status changed to failed. Please contact administrator."
            else:
                remaining_attempts = max_attempts - total_failed_attempts_now
                message = f"Exam failed! {remaining_attempts} attempt(s) remaining. Required score: {passing_percentage}%"

        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        db.commit()

        return {
            "exam_attempt_id": exam_attempt_id,
            "total_questions": exam_attempt.total_questions,
            "questions_attempted": exam_attempt.questions_attempted,
            "correct_answers": exam_attempt.correct_answers,
            "percentage_score": round(exam_attempt.percentage_score, 2),
            "total_marks": exam_attempt.total_marks,
            "status": exam_attempt.status,
            "passing_percentage": passing_percentage,
            "assignment_status": assignment.status,
            "failed_attempts": total_failed_attempts_now if exam_attempt.status == 'failed' else previous_failed_attempts,
            "max_attempts": max_attempts,
            "message": message,
            "details": details
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error submitting MCQ exam: {str(e)}")
@app.get("/mcq-exam/attempts")
def get_mcq_exam_attempts(
    assignment_id: Optional[int] = None,
    training_level_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get MCQ exam attempts for a user"""
    query = db.query(models.MCQExamAttempt)
    
    if current_user.role != models.UserRole.admin:
        # Employees can only see their own attempts
        query = query.join(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        )
    
    if assignment_id:
        query = query.filter(models.MCQExamAttempt.assignment_id == assignment_id)
    
    if training_level_id:
        query = query.filter(models.MCQExamAttempt.training_level_id == training_level_id)

    attempts = query.order_by(models.MCQExamAttempt.started_at.desc()).all()

    result = []
    for attempt in attempts:
        attempt_dict = {
            "id": attempt.id,
            "assignment_id": attempt.assignment_id,
            "training_level_id": attempt.training_level_id,
            "attempt_number": attempt.attempt_number,
            "total_questions": attempt.total_questions,
            "questions_attempted": attempt.questions_attempted,
            "correct_answers": attempt.correct_answers,
            "total_marks": attempt.total_marks,
            "percentage_score": attempt.percentage_score,
            "time_taken_seconds": attempt.time_taken_seconds,
            "status": attempt.status,
            "started_at": attempt.started_at,
            "completed_at": attempt.completed_at,
            "training_level_name": attempt.training_level.level_name,
            "passing_percentage": attempt.training_level.pass_percentage
        }
        result.append(attempt_dict)

    return result

@app.get("/mcq-exam/{exam_attempt_id}/result")
def get_mcq_exam_result(
    exam_attempt_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed result for a specific exam attempt"""
    exam_attempt = db.query(models.MCQExamAttempt).filter(
        models.MCQExamAttempt.id == exam_attempt_id
    ).first()

    if not exam_attempt:
        raise HTTPException(status_code=404, detail="Exam attempt not found")

    # Verify ownership
    if current_user.role != models.UserRole.admin and exam_attempt.assignment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this exam")

    # Get answers with questions
    answers = db.query(models.MCQAnswer).filter(
        models.MCQAnswer.exam_attempt_id == exam_attempt_id
    ).all()

    details = []
    for answer in answers:
        details.append({
            "question_id": answer.question.id,
            "question_text": answer.question.question_text,
            "options": {
                "A": answer.question.option_a,
                "B": answer.question.option_b,
                "C": answer.question.option_c,
                "D": answer.question.option_d
            },
            "correct_option": answer.question.correct_option,
            "selected_option": answer.selected_option,
            "is_correct": answer.is_correct,
            "explanation": answer.question.explanation,
            "marks_obtained": answer.marks_obtained,
            # Include image data in detailed results
            "question_image": answer.question.question_image,
            "option_images": {
                "A": answer.question.option_a_image,
                "B": answer.question.option_b_image,
                "C": answer.question.option_c_image,
                "D": answer.question.option_d_image
            }
        })

    return {
        "exam_attempt_id": exam_attempt.id,
        "assignment_id": exam_attempt.assignment_id,
        "training_level_id": exam_attempt.training_level_id,
        "training_level_name": exam_attempt.training_level.level_name,
        "attempt_number": exam_attempt.attempt_number,
        "total_questions": exam_attempt.total_questions,
        "questions_attempted": exam_attempt.questions_attempted,
        "correct_answers": exam_attempt.correct_answers,
        "percentage_score": round(exam_attempt.percentage_score, 2),
        "total_marks": exam_attempt.total_marks,
        "time_taken_seconds": exam_attempt.time_taken_seconds,
        "status": exam_attempt.status,
        "started_at": exam_attempt.started_at,
        "completed_at": exam_attempt.completed_at,
        "passing_percentage": exam_attempt.training_level.pass_percentage,
        "details": details
    }
# Update the complete-level endpoint to require passing MCQ exam
@app.put("/assignments/{assignment_id}/complete-level")
def complete_current_level_with_mcq(
    assignment_id: int,
    score_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Complete the current level - now requires passing MCQ exam"""
    try:
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == assignment_id
        ).first()

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        current_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == assignment.current_level_id
        ).first()

        if not current_level:
            raise HTTPException(status_code=404, detail="Current level not found")

        # Check if employee has passed MCQ exam for this level
        passed_exam = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.assignment_id == assignment_id,
            models.MCQExamAttempt.training_level_id == assignment.current_level_id,
            models.MCQExamAttempt.status == 'passed'
        ).first()

        if not passed_exam:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete level. Employee must pass the MCQ exam first. Required passing score: {current_level.pass_percentage}%"
            )

        # Rest of the existing complete-level logic...
        # ... [keep your existing complete-level logic here]

        return {
            "message": "Level completed successfully",
            "assignment_id": assignment_id,
            "mcq_score": passed_exam.percentage_score,
            "status": assignment.status,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error completing level: {str(e)}")
# ++++++++++++++++++++New endpoint for mcq_summary++++++++++++++++++++++++++++
@app.get("/assignments/{assignment_id}/mcq-summary")
def get_assignment_mcq_summary(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get MCQ exam summary for all levels in an assignment"""
    try:
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == assignment_id
        ).first()
        
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Authorization: admin or owner
        if current_user.role != models.UserRole.admin and assignment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized for this assignment")

        # Get all levels for this training
        levels = (
            db.query(models.TrainingLevel)
            .filter(models.TrainingLevel.training_id == assignment.training_id)
            .order_by(models.TrainingLevel.level_order.asc())
            .all()
        )

        result = []
        for level in levels:
            # Get the best exam attempt for this level
            best_attempt = (
                db.query(models.MCQExamAttempt)
                .filter(
                    models.MCQExamAttempt.assignment_id == assignment_id,
                    models.MCQExamAttempt.training_level_id == level.id,
                )
                .order_by(models.MCQExamAttempt.percentage_score.desc())
                .first()
            )

            if best_attempt:
                best_score = best_attempt.percentage_score
                rounded = int(round(best_score)) if best_score is not None else None
                passed = best_attempt.status == 'passed'
                
                item = {
                    "training_level_id": level.id,
                    "training_level_name": level.level_name,
                    "level_order": level.level_order,
                    "best_attempt_id": best_attempt.id,
                    "best_score": best_score,
                    "best_score_rounded": rounded,
                    "passed": passed,
                    "attempt_status": best_attempt.status,
                    "attempted_at": best_attempt.completed_at,
                }
            else:
                item = {
                    "training_level_id": level.id,
                    "training_level_name": level.level_name,
                    "level_order": level.level_order,
                    "best_attempt_id": None,
                    "best_score": None,
                    "best_score_rounded": None,
                    "passed": None,
                    "attempt_status": None,
                    "attempted_at": None,
                }

            result.append(item)

        return {
            "assignment_id": assignment_id,
            "training_id": assignment.training_id,
            "levels": result
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating MCQ summary: {str(e)}"
        )

# ++++++++++++++++++++ Certificate endpoint +++++++++++++++++++++++++++++
# New endpoint: Get all certificates for current user
@app.get("/certificates", response_model=List[schemas.CertificateResponse])
def get_all_my_certificates(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get all completion certificates for the current user's completed trainings"""
    try:
        # Get all completed assignments for the current user
        completed_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id,
            models.Assignment.status == models.TrainingStatus.completed
        ).all()
        
        if not completed_assignments:
            return []
        
        certificates_list = []
        
        for assignment in completed_assignments:
            # Fix: If status is completed but date is missing, set it now
            if not assignment.actual_completion_date:
                assignment.actual_completion_date = assignment.updated_at or datetime.utcnow()
                db.commit()
                db.refresh(assignment)
            
            # Check for existing certificate
            existing_certificate = db.query(models.Certificate).filter(
                models.Certificate.assignment_id == assignment.id
            ).first()
            
            if existing_certificate:
                certificate_id = existing_certificate.certificate_id
                completion_date = assignment.actual_completion_date
                issued_at = existing_certificate.issued_at
            else:
                # Generate new certificate
                completion_date = assignment.actual_completion_date
                certificate_id = f"CERT-{assignment.id}-{completion_date.strftime('%Y%m%d')}"
                
                # Save to DB
                new_certificate = models.Certificate(
                    certificate_id=certificate_id,
                    user_id=assignment.user_id,
                    training_id=assignment.training_id,
                    assignment_id=assignment.id,
                    completion_date=completion_date,
                    issued_at=datetime.utcnow()
                )
                db.add(new_certificate)
                try:
                    db.commit()
                    issued_at = new_certificate.issued_at
                except Exception as e:
                    db.rollback()
                    # If unique constraint fails (race condition), try to fetch existing
                    existing = db.query(models.Certificate).filter(
                        models.Certificate.assignment_id == assignment.id
                    ).first()
                    if existing:
                        certificate_id = existing.certificate_id
                        issued_at = existing.issued_at
                    else:
                        # Skip this certificate if there's an error
                        continue
            
            certificate_data = {
                "employee_name": assignment.user.full_name,
                "employee_code": assignment.user.user_code,
                "training_title": assignment.training.title,
                "training_category": assignment.training.category,
                "completion_date": completion_date,
                "certificate_id": certificate_id,
                "issued_at": issued_at,
                "congratulatory_message": "Congratulations on successfully completing this training program!"
            }
            
            certificates_list.append(certificate_data)
        
        return certificates_list
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving certificates: {str(e)}"
        )


#++++++++++++++++++++++++
@app.put("/users/{employee_id}", response_model=schemas.UserOut)
def update_user_employee(
    employee_id: int,
    user_update: schemas.UserUpdateEmployee,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Only employees (or admin) can access this endpoint
    if current_user.role not in {models.UserRole.admin, models.UserRole.employee}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only employees or admins can update"
        )
 
    db_user = (
        db.query(models.User)
        .filter(models.User.id == employee_id, models.User.is_active == True)
        .first()
    )
 
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) 
    update_data = user_update.dict(exclude_unset=True) 
    if "password" in update_data:
        db_user.password_hash = utils.hash_password(update_data.pop("password"))

    # Validate user_code uniqueness if being updated
    if "user_code" in update_data:
        new_user_code = update_data["user_code"]
        if not new_user_code.startswith("AASPL-"):
            new_user_code = f"AASPL-{new_user_code}"
            update_data["user_code"] = new_user_code
        existing_user = db.query(models.User).filter(
            models.User.user_code == new_user_code, models.User.id != employee_id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User code already exists"
            )

    allowed_fields = {"user_code", "designation", "department", "position", "password"}
 
    for key, value in update_data.items():
        if current_user.role == models.UserRole.employee and key not in allowed_fields:
            continue
        setattr(db_user, key, value) 
    db_user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_user)
 
    return db_user
 
@app.get("/reports/employee-engagement-detailed")
def get_employee_engagement_detailed(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed employee engagement analytics with level information"""
    try:
        # Get all active employees
        employees = db.query(models.User).filter(
            models.User.is_active == True,
            models.User.role == models.UserRole.employee
        ).all()

        engagement_data = []
        
        for employee in employees:
            # Get assignments for this employee
            assignments = db.query(models.Assignment).filter(
                models.Assignment.user_id == employee.id
            ).all()

            # Calculate engagement metrics
            total_assignments = len(assignments)
            completed_assignments = len([a for a in assignments if a.status == models.TrainingStatus.completed])
            in_progress_assignments = len([a for a in assignments if a.status == models.TrainingStatus.in_progress])
            
            # Get level progress details
            level_progress = []
            total_levels_completed = 0
            total_levels = 0
            
            for assignment in assignments:
                # Get all levels for this training
                training_levels = db.query(models.TrainingLevel).filter(
                    models.TrainingLevel.training_id == assignment.training_id
                ).order_by(models.TrainingLevel.level_order.asc()).all()
                
                total_levels += len(training_levels)
                
                # Get level dates
                level_dates = db.query(models.AssignmentLevelDate).filter(
                    models.AssignmentLevelDate.assignment_id == assignment.id
                ).all()
                
                level_date_dict = {ld.level_id: ld for ld in level_dates}
                
                # Check completed levels
                evaluations = db.query(models.Evaluation).filter(
                    models.Evaluation.assignment_id == assignment.id,
                    models.Evaluation.status == models.EvaluationStatus.passed
                ).all()
                
                completed_level_ids = [eval.training_level_id for eval in evaluations]
                total_levels_completed += len(completed_level_ids)
                
                # Add level progress info
                for level in training_levels:
                    level_date = level_date_dict.get(level.id)
                    level_progress.append({
                        "training_title": assignment.training.title,
                        "level_name": level.level_name,
                        "level_order": level.level_order,
                        "status": "completed" if level.id in completed_level_ids else "in_progress" if assignment.current_level_id == level.id else "not_started",
                        "start_date": level_date.start_date if level_date else assignment.training_start_date,
                        "due_date": level_date.due_date if level_date else assignment.training_end_date,
                        "actual_completion_date": next((eval.evaluation_date for eval in evaluations if eval.training_level_id == level.id), None),
                        "pass_percentage": level.pass_percentage
                    })

            # Calculate engagement score
            engagement_score = 0
            if total_assignments > 0:
                completion_weight = (completed_assignments / total_assignments) * 40
                progress_weight = (in_progress_assignments / total_assignments) * 30
                level_weight = (total_levels_completed / total_levels * 30) if total_levels > 0 else 0
                engagement_score = completion_weight + progress_weight + level_weight

            engagement_data.append({
                "employee_id": employee.id,
                "employee_name": f"{employee.first_name} {employee.last_name}",
                "employee_code": employee.user_code,
                "department": employee.department,
                "designation": employee.designation,
                "total_assignments": total_assignments,
                "completed_assignments": completed_assignments,
                "in_progress_assignments": in_progress_assignments,
                "not_started_assignments": total_assignments - completed_assignments - in_progress_assignments,
                "engagement_score": round(engagement_score, 2),
                "completion_rate": round((completed_assignments / total_assignments * 100) if total_assignments > 0 else 0, 2),
                "level_completion_rate": round((total_levels_completed / total_levels * 100) if total_levels > 0 else 0, 2),
                "total_levels": total_levels,
                "completed_levels": total_levels_completed,
                "level_progress": level_progress,
                "recent_activity": assignment.updated_at if assignments else None
            })

        # Sort by engagement score
        engagement_data.sort(key=lambda x: x["engagement_score"], reverse=True)

        # Calculate overall statistics
        total_employees = len(engagement_data)
        active_employees = len([e for e in engagement_data if e["total_assignments"] > 0])
        departments = len(set([e["department"] for e in engagement_data if e["department"]]))
        total_recent_activity = len([e for e in engagement_data if e["recent_activity"] and (datetime.utcnow() - e["recent_activity"]).days <= 30])
        
        overall_engagement = round(sum(e["engagement_score"] for e in engagement_data) / len(engagement_data) if engagement_data else 0, 2)
        overall_completion = round(sum(e["completion_rate"] for e in engagement_data) / len(engagement_data) if engagement_data else 0, 2)

        return {
            "summary": {
                "total_employees": total_employees,
                "active_employees": active_employees,
                "departments": departments,
                "recent_activity": total_recent_activity,
                "overall_engagement": overall_engagement,
                "overall_completion": overall_completion
            },
            "employee_details": engagement_data
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating employee engagement report: {str(e)}"
        )

@app.get("/reports/training-programs-detailed")
def get_training_programs_detailed(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed training programs analytics with level information"""
    try:
        # Get all trainings
        trainings = db.query(models.Training).all()
        
        training_data = []
        
        for training in trainings:
            # Get all assignments for this training
            assignments = db.query(models.Assignment).filter(
                models.Assignment.training_id == training.id
            ).all()
            
            # Get all levels for this training
            levels = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.training_id == training.id
            ).order_by(models.TrainingLevel.level_order.asc()).all()
            
            # Calculate basic metrics
            total_assignments = len(assignments)
            completed_assignments = len([a for a in assignments if a.status == models.TrainingStatus.completed])
            in_progress_assignments = len([a for a in assignments if a.status == models.TrainingStatus.in_progress])
            assigned_assignments = len([a for a in assignments if a.status == models.TrainingStatus.assigned])
            
            # Level-wise analytics
            level_analytics = []
            total_level_completions = 0
            
            for level in levels:
                # Count employees at this level
                employees_at_level = len([a for a in assignments if a.current_level_id == level.id])
                
                # Count completed this level
                level_completions = db.query(models.Evaluation).filter(
                    models.Evaluation.training_level_id == level.id,
                    models.Evaluation.status == models.EvaluationStatus.passed
                ).count()
                
                total_level_completions += level_completions
                
                # Get average score for this level
                avg_score_result = db.query(func.avg(models.Evaluation.total_score)).filter(
                    models.Evaluation.training_level_id == level.id,
                    models.Evaluation.total_score.isnot(None)
                ).scalar()
                
                level_analytics.append({
                    "level_id": level.id,
                    "level_name": level.level_name,
                    "level_order": level.level_order,
                    "employees_at_level": employees_at_level,
                    "completions": level_completions,
                    "completion_rate": round((level_completions / total_assignments * 100) if total_assignments > 0 else 0, 2),
                    "avg_score": round(avg_score_result or 0, 2),
                    "pass_percentage": level.pass_percentage,
                    "duration_hours": level.duration_hours
                })
            
            # Department breakdown
            department_breakdown = db.query(
                models.User.department,
                func.count(models.Assignment.id).label('count')
            ).join(
                models.Assignment, models.Assignment.user_id == models.User.id
            ).filter(
                models.Assignment.training_id == training.id
            ).group_by(models.User.department).all()
            
            dept_data = [{"department": dept.department, "count": dept.count} for dept in department_breakdown]
            
            # Time analytics
            avg_completion_days = None
            if completed_assignments > 0:
                completion_times = []
                for assignment in assignments:
                    if assignment.status == models.TrainingStatus.completed and assignment.training_start_date and assignment.actual_completion_date:
                        days = (assignment.actual_completion_date - assignment.training_start_date).days
                        completion_times.append(days)
                
                if completion_times:
                    avg_completion_days = round(sum(completion_times) / len(completion_times), 1)
            
            training_data.append({
                "training_id": training.id,
                "title": training.title,
                "category": training.category,
                "description": training.description,
                "total_assignments": total_assignments,
                "completed_assignments": completed_assignments,
                "in_progress_assignments": in_progress_assignments,
                "assigned_assignments": assigned_assignments,
                "completion_rate": round((completed_assignments / total_assignments * 100) if total_assignments > 0 else 0, 2),
                "progress_rate": round(((completed_assignments + in_progress_assignments) / total_assignments * 100) if total_assignments > 0 else 0, 2),
                "total_levels": len(levels),
                "total_level_completions": total_level_completions,
                "level_completion_rate": round((total_level_completions / (len(levels) * total_assignments) * 100) if total_assignments > 0 else 0, 2),
                "level_analytics": level_analytics,
                "department_breakdown": dept_data,
                "avg_completion_days": avg_completion_days,
                "total_categories": len(set([t.category for t in trainings]))
            })
        
        return training_data
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating training programs report: {str(e)}"
        )

@app.get("/reports/completion-performance-detailed")
def get_completion_performance_detailed(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed completion performance analytics"""
    try:
        # Get all assignments with their evaluations
        assignments = db.query(models.Assignment).all()
        
        completion_data = {
            "summary": {
                "total_assignments": len(assignments),
                "completed": 0,
                "in_progress": 0,
                "assigned": 0,
                "failed": 0,
                "completion_rate": 0,
                "success_rate": 0,
                "avg_score": 0
            },
            "level_breakdown": [],
            "time_analysis": [],
            "score_distribution": []
        }
        
        # Calculate basic metrics
        for assignment in assignments:
            if assignment.status == models.TrainingStatus.completed:
                completion_data["summary"]["completed"] += 1
            elif assignment.status == models.TrainingStatus.in_progress:
                completion_data["summary"]["in_progress"] += 1
            elif assignment.status == models.TrainingStatus.assigned:
                completion_data["summary"]["assigned"] += 1
            elif assignment.status == models.TrainingStatus.failed:
                completion_data["summary"]["failed"] += 1
        
        total_assignments = completion_data["summary"]["total_assignments"]
        completion_data["summary"]["completion_rate"] = round(
            (completion_data["summary"]["completed"] / total_assignments * 100) if total_assignments > 0 else 0, 2
        )
        completion_data["summary"]["success_rate"] = round(
            (100 - (completion_data["summary"]["failed"] / total_assignments * 100)) if total_assignments > 0 else 100, 2
        )
        
        # Level completion analysis
        levels = db.query(models.TrainingLevel).all()
        for level in levels:
            total_assignments_for_level = len([a for a in assignments if a.training_id == level.training_id])
            level_completions = db.query(models.Evaluation).filter(
                models.Evaluation.training_level_id == level.id,
                models.Evaluation.status == models.EvaluationStatus.passed
            ).count()
            
            completion_data["level_breakdown"].append({
                "level_id": level.id,
                "level_name": level.level_name,
                "training_title": level.training.title,
                "total_assignments": total_assignments_for_level,
                "completions": level_completions,
                "completion_rate": round((level_completions / total_assignments_for_level * 100) if total_assignments_for_level > 0 else 0, 2),
                "pass_percentage": level.pass_percentage
            })
        
        # Time analysis (last 6 months)
        for i in range(6):
            month = datetime.utcnow().replace(day=1) - timedelta(days=30*i)
            month_start = month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            next_month = (month + timedelta(days=32)).replace(day=1)
            
            monthly_completions = len([a for a in assignments 
                                    if a.actual_completion_date 
                                    and month_start <= a.actual_completion_date < next_month])
            
            completion_data["time_analysis"].append({
                "month": month.strftime("%Y-%m"),
                "completions": monthly_completions,
                "month_name": month.strftime("%b %Y")
            })
        
        completion_data["time_analysis"].reverse()
        
        # Score distribution
        evaluations = db.query(models.Evaluation).filter(
            models.Evaluation.total_score.isnot(None)
        ).all()
        
        if evaluations:
            scores = [e.total_score for e in evaluations]
            completion_data["summary"]["avg_score"] = round(sum(scores) / len(scores), 2)
            
            # Score ranges
            ranges = ["0-40", "41-60", "61-75", "76-90", "91-100"]
            for score_range in ranges:
                low, high = map(int, score_range.split('-'))
                count = len([s for s in scores if low <= s <= high])
                completion_data["score_distribution"].append({
                    "range": score_range,
                    "count": count,
                    "percentage": round((count / len(scores) * 100), 2)
                })
        
        return completion_data
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating completion performance report: {str(e)}"
        )
# Add this endpoint to your main.py

@app.get("/reports/employee-training-details/{employee_id}")
def get_employee_training_details(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed training information for a specific employee"""
    try:
        # Verify employee exists and is active
        employee = db.query(models.User).filter(
            models.User.id == employee_id,
            models.User.is_active == True,
            models.User.role == models.UserRole.employee
        ).first()

        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")

        # Get all assignments for this employee
        assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == employee_id
        ).all()

        training_details = []
        total_levels_completed = 0
        total_levels = 0
        overall_avg_score = 0
        valid_scores = 0

        for assignment in assignments:
            # Get training information
            training = assignment.training
            
            # Get all levels for this training
            training_levels = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.training_id == training.id
            ).order_by(models.TrainingLevel.level_order.asc()).all()

            # Get level dates
            level_dates = db.query(models.AssignmentLevelDate).filter(
                models.AssignmentLevelDate.assignment_id == assignment.id
            ).all()
            level_date_dict = {ld.level_id: ld for ld in level_dates}

            # Get evaluations for this assignment
            evaluations = db.query(models.Evaluation).filter(
                models.Evaluation.assignment_id == assignment.id
            ).all()
            evaluation_dict = {eval.training_level_id: eval for eval in evaluations}

            # Calculate level progress
            level_progress = []
            levels_completed = 0
            training_total_score = 0
            training_valid_scores = 0

            for level in training_levels:
                evaluation = evaluation_dict.get(level.id)
                level_date = level_date_dict.get(level.id)
                
                # Determine level status
                if evaluation and evaluation.status == models.EvaluationStatus.passed:
                    status = "completed"
                    levels_completed += 1
                    total_levels_completed += 1
                elif assignment.current_level_id == level.id:
                    status = "current"
                elif level.level_order < assignment.current_level.level_order:
                    status = "completed"  # Assumed completed if passed previous levels
                    levels_completed += 1
                    total_levels_completed += 1
                else:
                    status = "not_started"

                # Calculate scores
                level_score = None
                if evaluation:
                    if evaluation.total_score and 0 <= evaluation.total_score <= 100:
                        level_score = evaluation.total_score
                        training_total_score += level_score
                        training_valid_scores += 1
                    elif evaluation.mcq_score and 0 <= evaluation.mcq_score <= 100:
                        level_score = evaluation.mcq_score
                        training_total_score += level_score
                        training_valid_scores += 1

                level_progress.append({
                    "level_id": level.id,
                    "level_name": level.level_name,
                    "level_order": level.level_order,
                    "status": status,
                    "score": level_score,
                    "pass_percentage": level.pass_percentage,
                    "start_date": level_date.start_date if level_date else assignment.training_start_date,
                    "due_date": level_date.due_date if level_date else assignment.training_end_date,
                    "actual_completion_date": evaluation.evaluation_date if evaluation else None,
                    "evaluation_status": evaluation.status if evaluation else None,
                    "max_attempts": level.max_attempts,
                    "duration_hours": level.duration_hours
                })

            total_levels += len(training_levels)
            
            # Calculate training average score
            training_avg_score = round(training_total_score / training_valid_scores, 2) if training_valid_scores > 0 else None
            if training_avg_score:
                overall_avg_score += training_avg_score
                valid_scores += 1

            # Get MCQ exam attempts for this assignment
            mcq_attempts = db.query(models.MCQExamAttempt).filter(
                models.MCQExamAttempt.assignment_id == assignment.id
            ).order_by(models.MCQExamAttempt.started_at.desc()).all()

            mcq_summary = []
            for attempt in mcq_attempts:
                level_name = attempt.training_level.level_name if attempt.training_level else "Unknown"
                mcq_summary.append({
                    "attempt_id": attempt.id,
                    "training_level_id": attempt.training_level_id,
                    "training_level_name": level_name,
                    "attempt_number": attempt.attempt_number,
                    "percentage_score": attempt.percentage_score,
                    "status": attempt.status,
                    "total_questions": attempt.total_questions,
                    "correct_answers": attempt.correct_answers,
                    "time_taken_seconds": attempt.time_taken_seconds,
                    "started_at": attempt.started_at,
                    "completed_at": attempt.completed_at
                })

            training_details.append({
                "assignment_id": assignment.id,
                "training_id": training.id,
                "training_title": training.title,
                "training_category": training.category,
                "training_description": training.description,
                "assignment_status": assignment.status,
                "training_start_date": assignment.training_start_date,
                "training_end_date": assignment.training_end_date,
                "actual_completion_date": assignment.actual_completion_date,
                "current_level_id": assignment.current_level_id,
                "current_level_name": assignment.current_level.level_name if assignment.current_level else "Unknown",
                "total_levels": len(training_levels),
                "levels_completed": levels_completed,
                "completion_percentage": round((levels_completed / len(training_levels) * 100) if training_levels else 0, 2),
                "average_score": training_avg_score,
                "level_progress": level_progress,
                "mcq_exam_attempts": mcq_summary,
                "overall_progress": f"{levels_completed}/{len(training_levels)} levels",
                "days_until_due": (assignment.training_end_date - datetime.utcnow()).days if assignment.training_end_date else None
            })

        # Calculate overall statistics
        overall_avg_score = round(overall_avg_score / valid_scores, 2) if valid_scores > 0 else 0
        overall_completion_rate = round((total_levels_completed / total_levels * 100) if total_levels > 0 else 0, 2)

        return {
            "employee_info": {
                "employee_id": employee.id,
                "employee_name": f"{employee.first_name} {employee.last_name}",
                "employee_code": employee.user_code,
                "department": employee.department,
                "designation": employee.designation,
                "email": employee.email,
                "is_active": employee.is_active
            },
            "summary": {
                "total_trainings": len(assignments),
                "completed_trainings": len([a for a in assignments if a.status == models.TrainingStatus.completed]),
                "in_progress_trainings": len([a for a in assignments if a.status == models.TrainingStatus.in_progress]),
                "assigned_trainings": len([a for a in assignments if a.status == models.TrainingStatus.assigned]),
                "total_levels": total_levels,
                "completed_levels": total_levels_completed,
                "overall_completion_rate": overall_completion_rate,
                "overall_avg_score": overall_avg_score,
                "total_mcq_attempts": sum(len(training["mcq_exam_attempts"]) for training in training_details)
            },
            "training_details": training_details
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating employee training details: {str(e)}"
        )
@app.get("/reports/training-employees/{training_id}")
def get_training_employees(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get detailed employee assignments for a specific training"""
    try:
        # Get training information
        training = db.query(models.Training).filter(models.Training.id == training_id).first()
        if not training:
            raise HTTPException(status_code=404, detail="Training not found")

        # Get all assignments for this training
        assignments = db.query(models.Assignment).filter(
            models.Assignment.training_id == training_id
        ).all()

        employees_data = []
        for assignment in assignments:
            employee = assignment.user
            
            # Get current level
            current_level = assignment.current_level
            
            # Calculate completion percentage
            training_levels = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.training_id == training_id
            ).all()
            
            completed_evaluations = db.query(models.Evaluation).filter(
                models.Evaluation.assignment_id == assignment.id,
                models.Evaluation.status == models.EvaluationStatus.passed
            ).count()
            
            completion_percentage = round(
                (completed_evaluations / len(training_levels) * 100) if training_levels else 0, 2
            )
            
            # Calculate average score
            evaluations = db.query(models.Evaluation).filter(
                models.Evaluation.assignment_id == assignment.id
            ).all()
            
            valid_scores = [e.total_score for e in evaluations if e.total_score is not None and 0 <= e.total_score <= 100]
            average_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None

            employees_data.append({
                "assignment_id": assignment.id,
                "employee_id": employee.id,
                "employee_name": f"{employee.first_name} {employee.last_name}",
                "employee_code": employee.user_code,
                "department": employee.department,
                "designation": employee.designation,
                "assignment_status": assignment.status.value,
                "current_level_id": assignment.current_level_id,
                "current_level_name": current_level.level_name,
                "training_start_date": assignment.training_start_date,
                "training_end_date": assignment.training_end_date,
                "completion_percentage": completion_percentage,
                "average_score": average_score,
                "actual_completion_date": assignment.actual_completion_date
            })

        # Get level breakdown
        levels = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == training_id
        ).order_by(models.TrainingLevel.level_order.asc()).all()
        
        level_breakdown = []
        for level in levels:
            employee_count = len([a for a in assignments if a.current_level_id == level.id])
            level_breakdown.append({
                "level_id": level.id,
                "level_name": level.level_name,
                "level_order": level.level_order,
                "employee_count": employee_count
            })

        return {
            "training_info": {
                "training_id": training.id,
                "title": training.title,
                "category": training.category,
                "description": training.description,
                "total_employees": len(employees_data)
            },
            "employees": employees_data,
            "level_breakdown": level_breakdown
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching training employees: {str(e)}"
        )

@app.get("/export/training-employees/{training_id}")
def export_training_employees(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Export training employee data to Excel"""
    try:
        # Get the data
        training_data = get_training_employees(training_id, db, current_user)
        
        # Create Excel file
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            
            # Define formats
            header_format = workbook.add_format({
                'bold': True,
                'font_color': 'white',
                'bg_color': '#2E86AB',
                'border': 1,
                'align': 'center'
            })
            
            cell_format = workbook.add_format({
                'border': 1,
                'align': 'left'
            })
            
            # Create main data worksheet
            df = pd.DataFrame(training_data["employees"])
            
            # Select and rename columns for export
            export_columns = {
                'employee_name': 'Employee Name',
                'employee_code': 'Employee Code',
                'department': 'Department',
                'designation': 'Designation',
                'assignment_status': 'Status',
                'current_level_name': 'Current Level',
                'completion_percentage': 'Completion %',
                'average_score': 'Average Score',
                'training_start_date': 'Start Date',
                'training_end_date': 'End Date'
            }
            
            df_export = df[list(export_columns.keys())].rename(columns=export_columns)
            df_export['Start Date'] = pd.to_datetime(df_export['Start Date']).dt.strftime('%Y-%m-%d')
            df_export['End Date'] = pd.to_datetime(df_export['End Date']).dt.strftime('%Y-%m-%d')
            
            # Write to Excel
            df_export.to_excel(writer, sheet_name='Employees', index=False, startrow=1)
            
            worksheet = writer.sheets['Employees']
            
            # Write title
            worksheet.merge_range('A1:J1', f"Training: {training_data['training_info']['title']}", header_format)
            
            # Format headers
            for col_num, value in enumerate(df_export.columns.values):
                worksheet.write(1, col_num, value, header_format)
            
            # Adjust column widths
            worksheet.set_column('A:A', 25)
            worksheet.set_column('B:B', 15)
            worksheet.set_column('C:C', 20)
            worksheet.set_column('D:D', 20)
            worksheet.set_column('E:E', 15)
            worksheet.set_column('F:F', 20)
            worksheet.set_column('G:G', 12)
            worksheet.set_column('H:H', 12)
            worksheet.set_column('I:J', 12)
            
            # Create summary worksheet
            summary_data = {
                'Metric': ['Total Employees', 'Completed', 'In Progress', 'Assigned', 'Overall Completion Rate'],
                'Value': [
                    len(training_data["employees"]),
                    len([e for e in training_data["employees"] if e['assignment_status'] == 'completed']),
                    len([e for e in training_data["employees"] if e['assignment_status'] == 'in_progress']),
                    len([e for e in training_data["employees"] if e['assignment_status'] == 'assigned']),
                    f"{round((len([e for e in training_data['employees'] if e['assignment_status'] == 'completed']) / len(training_data['employees'])) * 100, 2)}%"
                ]
            }
            
            df_summary = pd.DataFrame(summary_data)
            df_summary.to_excel(writer, sheet_name='Summary', index=False)
            
            summary_worksheet = writer.sheets['Summary']
            summary_worksheet.set_column('A:A', 25)
            summary_worksheet.set_column('B:B', 15)

        output.seek(0)
        
        filename = f"training_employees_{training_data['training_info']['title'].replace(' ', '_')}.xlsx"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue()),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error exporting training employees: {str(e)}"
        )
@app.get("/reports/top-employees", response_model=list[dict])
def get_top_employees(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Returns Top 5 Employees showing only:
    - Full Name
    - Department
    - Average Score
    """
    try:
        # Calculate average scores directly in SQL for better performance
        top_employees_query = (
            db.query(
                models.User.full_name.label("full_name"),
                models.User.department.label("department"),
                func.avg(models.Evaluation.total_score).label("average_score")
            )
            .join(models.Assignment, models.Assignment.user_id == models.User.id)
            .join(models.Evaluation, models.Evaluation.assignment_id == models.Assignment.id)
            .filter(
                models.User.role == models.UserRole.employee,
                models.User.is_active == True,
                models.Evaluation.total_score.isnot(None)
            )
            .group_by(models.User.id)
            .order_by(func.avg(models.Evaluation.total_score).desc())
            .limit(5)  # ✅ Enforced at the database level
            .all()
        )

        # Build response
        result = [
            {
                "full_name": emp.full_name,
                "department": emp.department or "—",
                "average_score": round(emp.average_score or 0.0, 2),
            }
            for emp in top_employees_query
        ]

        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching top employees: {str(e)}"
        )
@app.get("/reports/top-employees-by-training")
def get_top_employees_by_training(
    training_id: Optional[int] = None,
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get top performing employees categorized by training program"""
    try:
        # Base query for all trainings
        trainings_query = db.query(models.Training)
        
        if training_id:
            trainings_query = trainings_query.filter(models.Training.id == training_id)
        
        trainings = trainings_query.all()
        
        result = []
        
        for training in trainings:
            # Get top employees for this specific training
            top_employees = (
                db.query(
                    models.User.id.label("employee_id"),
                    models.User.first_name,
                    models.User.last_name,
                    models.User.designation,
                    models.User.department,
                    # Training-specific metrics
                    func.count(
                        case(
                            (
                                and_(
                                    models.Assignment.training_id == training.id,
                                    models.Assignment.status == models.TrainingStatus.completed
                                ),
                                1
                            ),
                            else_=None
                        )
                    ).label("completed_trainings"),
                    func.avg(
                        case(
                            (
                                and_(
                                    models.Evaluation.total_score >= 0,
                                    models.Evaluation.total_score <= 100,
                                    models.Assignment.training_id == training.id
                                ),
                                models.Evaluation.total_score
                            ),
                            else_=None
                        )
                    ).label("avg_score"),
                    # Count total levels completed in this training
                    func.count(
                        case(
                            (
                                and_(
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Assignment.training_id == training.id
                                ),
                                models.Evaluation.id
                            ),
                            else_=None
                        )
                    ).label("levels_completed")
                )
                .join(models.Assignment, models.Assignment.user_id == models.User.id)
                .outerjoin(
                    models.Evaluation,
                    models.Evaluation.assignment_id == models.Assignment.id,
                )
                .filter(
                    models.User.is_active == True,
                    models.User.role == models.UserRole.employee,
                    models.Assignment.training_id == training.id  # Filter by specific training
                )
                .group_by(
                    models.User.id,
                    models.User.first_name,
                    models.User.last_name,
                    models.User.designation,
                    models.User.department,
                )
                .having(func.count(models.Assignment.id) > 0)  # Only employees with assignments in this training
                .order_by(
                    func.avg(
                        case(
                            (
                                and_(
                                    models.Evaluation.total_score >= 0,
                                    models.Evaluation.total_score <= 100,
                                    models.Assignment.training_id == training.id
                                ),
                                models.Evaluation.total_score
                            ),
                            else_=None
                        )
                    ).desc().nullslast(),
                    func.count(
                        case(
                            (
                                and_(
                                    models.Evaluation.status == models.EvaluationStatus.passed,
                                    models.Assignment.training_id == training.id
                                ),
                                models.Evaluation.id
                            ),
                            else_=None
                        )
                    ).desc()
                )
                .limit(limit)
                .all()
            )
            
            training_employees = []
            for emp in top_employees:
                avg_score = emp.avg_score or 0
                training_employees.append({
                    "employee_id": emp.employee_id,
                    "name": f"{emp.first_name} {emp.last_name}",
                    "designation": emp.designation or "Employee",
                    "department": emp.department,
                    "completed_trainings": emp.completed_trainings or 0,
                    "levels_completed": emp.levels_completed or 0,
                    "avg_score": round(avg_score, 2),
                })
            
            if training_employees:  # Only include trainings with employees
                result.append({
                    "training_id": training.id,
                    "training_title": training.title,
                    "training_category": training.category,
                    "total_employees": len(training_employees),
                    "top_employees": training_employees
                })
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating top employees by training report: {str(e)}",
        )
#+++++++++++++
import os
import uuid
from fastapi import UploadFile, File, Form

from fastapi.staticfiles import StaticFiles

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
# File upload configuration
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
ALLOWED_DOC_TYPES = ["application/pdf", "application/msword", 
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# ---------------- Employee Report Endpoints ----------------

@app.post("/employee/reports/create", response_model=ReportResponse)
async def create_employee_report(
    report_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    priority: str = Form("medium"),
    attachment: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Employee endpoint to create issue reports or feedback
    """
    try:
        # Validate report type
        if report_type not in ["issue", "feedback"]:
            raise HTTPException(status_code=400, detail="Invalid report type")
        
        # Check for duplicate issues (only for issue type)
        if report_type == "issue":
            existing_issue = db.query(models.UserReport).filter(
                models.UserReport.title == title,
                models.UserReport.report_type == "issue",
                models.UserReport.status.in_(["pending", "in_progress"])
            ).first()
            
            if existing_issue:
                raise HTTPException(
                    status_code=400, 
                    detail="Similar issue already exists and is being addressed"
                )
        
        # Handle file upload
        attachment_url = None
        attachment_type = None
        original_filename = None
        
        if attachment:
            # Validate file size
            if attachment.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="File size too large. Maximum 10MB allowed.")
            
            # Validate file type
            if attachment.content_type not in ALLOWED_IMAGE_TYPES + ALLOWED_DOC_TYPES:
                raise HTTPException(status_code=400, detail="File type not allowed")
            
            # Generate unique filename
            file_extension = os.path.splitext(attachment.filename)[1]
            unique_filename = f"{uuid.uuid4()}{file_extension}"
            
            # Determine attachment type
            if attachment.content_type in ALLOWED_IMAGE_TYPES:
                attachment_type = "image"
            else:
                attachment_type = "document"
            
            # Save file
            upload_dir = "uploads/reports"
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, unique_filename)
            
            with open(file_path, "wb") as buffer:
                content = await attachment.read()
                buffer.write(content)
            
            attachment_url = f"/{file_path}"
            original_filename = attachment.filename
        
        # Create report
        new_report = models.UserReport(
            user_id=current_user.id,
            report_type=report_type,
            title=title,
            description=description,
            priority=priority,
            attachment_url=attachment_url,
            attachment_type=attachment_type,
            original_filename=original_filename
        )
        
        db.add(new_report)
        db.commit()
        db.refresh(new_report)
        
        # Get comment count
        comment_count = db.query(models.ReportComment).filter(
            models.ReportComment.report_id == new_report.id
        ).count()
        
        return {
            "id": new_report.id,
            "user_id": new_report.user_id,
            "user_name": f"{current_user.first_name} {current_user.last_name}",
            "report_type": new_report.report_type,
            "title": new_report.title,
            "description": new_report.description,
            "status": new_report.status,
            "priority": new_report.priority,
            "attachment_url": new_report.attachment_url,
            "attachment_type": new_report.attachment_type,
            "original_filename": new_report.original_filename,
            "created_at": new_report.created_at,
            "updated_at": new_report.updated_at,
            "comment_count": comment_count
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

@app.get("/employee/reports/my-reports", response_model=List[ReportResponse])
def get_my_reports(
    report_type: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Employee endpoint to get their own reports
    """
    try:
        query = db.query(models.UserReport).filter(
            models.UserReport.user_id == current_user.id
        )
        
        if report_type:
            query = query.filter(models.UserReport.report_type == report_type)
        
        if status:
            query = query.filter(models.UserReport.status == status)
        
        reports = query.order_by(models.UserReport.created_at.desc()).all()
        
        result = []
        for report in reports:
            # Get comment count for each report
            comment_count = db.query(models.ReportComment).filter(
                models.ReportComment.report_id == report.id
            ).count()
            
            result.append({
                "id": report.id,
                "user_id": report.user_id,
                "user_name": f"{report.user.first_name} {report.user.last_name}",
                "report_type": report.report_type,
                "title": report.title,
                "description": report.description,
                "status": report.status,
                "priority": report.priority,
                "attachment_url": report.attachment_url,
                "attachment_type": report.attachment_type,
                "original_filename": report.original_filename,
                "created_at": report.created_at,
                "updated_at": report.updated_at,
                "comment_count": comment_count
            })
        
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

@app.get("/employee/reports/{report_id}", response_model=ReportDetailResponse)
def get_my_report_detail(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Employee endpoint to get detailed view of their specific report
    """
    try:
        report = db.query(models.UserReport).filter(
            models.UserReport.id == report_id,
            models.UserReport.user_id == current_user.id
        ).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Get comments for this report
        comments = db.query(models.ReportComment).filter(
            models.ReportComment.report_id == report_id
        ).order_by(models.ReportComment.created_at.asc()).all()
        
        comment_list = []
        for comment in comments:
            comment_list.append({
                "id": comment.id,
                "report_id": comment.report_id,
                "user_id": comment.user_id,
                "user_name": f"{comment.user.first_name} {comment.user.last_name}",
                "comment_text": comment.comment_text,
                "attachment_url": comment.attachment_url,
                "created_at": comment.created_at
            })
        
        return {
            "id": report.id,
            "user_id": report.user_id,
            "user_name": f"{report.user.first_name} {report.user.last_name}",
            "report_type": report.report_type,
            "title": report.title,
            "description": report.description,
            "status": report.status,
            "priority": report.priority,
            "attachment_url": report.attachment_url,
            "attachment_type": report.attachment_type,
            "original_filename": report.original_filename,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "comment_count": len(comment_list),
            "comments": comment_list
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching report details: {str(e)}")

# ---------------- Admin Report Endpoints ----------------

@app.get("/admin/reports/all", response_model=List[ReportResponse])
def get_all_reports_admin(
    report_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Admin endpoint to get all reports from all employees
    """
    try:
        query = db.query(models.UserReport)
        
        if report_type:
            query = query.filter(models.UserReport.report_type == report_type)
        
        if status:
            query = query.filter(models.UserReport.status == status)
            
        if priority:
            query = query.filter(models.UserReport.priority == priority)
        
        reports = query.order_by(
            models.UserReport.created_at.desc()
        ).all()
        
        result = []
        for report in reports:
            # Get comment count for each report
            comment_count = db.query(models.ReportComment).filter(
                models.ReportComment.report_id == report.id
            ).count()
            
            result.append({
                "id": report.id,
                "user_id": report.user_id,
                "user_name": f"{report.user.first_name} {report.user.last_name}",
                "report_type": report.report_type,
                "title": report.title,
                "description": report.description,
                "status": report.status,
                "priority": report.priority,
                "attachment_url": report.attachment_url,
                "attachment_type": report.attachment_type,
                "original_filename": report.original_filename,
                "created_at": report.created_at,
                "updated_at": report.updated_at,
                "comment_count": comment_count
            })
        
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

@app.get("/admin/reports/{report_id}", response_model=ReportDetailResponse)
def get_report_detail_admin(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Admin endpoint to get detailed view of any report
    """
    try:
        report = db.query(models.UserReport).filter(
            models.UserReport.id == report_id
        ).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Get comments for this report
        comments = db.query(models.ReportComment).filter(
            models.ReportComment.report_id == report_id
        ).order_by(models.ReportComment.created_at.asc()).all()
        
        comment_list = []
        for comment in comments:
            comment_list.append({
                "id": comment.id,
                "report_id": comment.report_id,
                "user_id": comment.user_id,
                "user_name": f"{comment.user.first_name} {comment.user.last_name}",
                "comment_text": comment.comment_text,
                "attachment_url": comment.attachment_url,
                "created_at": comment.created_at
            })
        
        return {
            "id": report.id,
            "user_id": report.user_id,
            "user_name": f"{report.user.first_name} {report.user.last_name}",
            "report_type": report.report_type,
            "title": report.title,
            "description": report.description,
            "status": report.status,
            "priority": report.priority,
            "attachment_url": report.attachment_url,
            "attachment_type": report.attachment_type,
            "original_filename": report.original_filename,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "comment_count": len(comment_list),
            "comments": comment_list
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching report details: {str(e)}")

@app.put("/admin/reports/{report_id}/status")
def update_report_status_admin(
    report_id: int,
    status_update: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Admin endpoint to update report status
    """
    try:
        report = db.query(models.UserReport).filter(
            models.UserReport.id == report_id
        ).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        new_status = status_update.get("status")
        valid_statuses = ["pending", "in_progress", "resolved", "closed"]
        
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Invalid status")
        
        report.status = new_status
        report.updated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "message": f"Report status updated to {new_status}",
            "report_id": report_id,
            "new_status": new_status
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating report status: {str(e)}")

@app.post("/admin/reports/{report_id}/comments")
def add_report_comment_admin(
    report_id: int,
    comment_data: CommentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """
    Admin endpoint to add comments to reports
    """
    try:
        report = db.query(models.UserReport).filter(
            models.UserReport.id == report_id
        ).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        new_comment = models.ReportComment(
            report_id=report_id,
            user_id=current_user.id,
            comment_text=comment_data.comment_text
        )
        
        db.add(new_comment)
        db.commit()
        db.refresh(new_comment)
        
        return {
            "message": "Comment added successfully",
            "comment_id": new_comment.id,
            "report_id": report_id
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error adding comment: {str(e)}")

#+++++++++
#++++++++++

# Violation types - no face detection
class ViolationTypes(str, Enum):
    MULTIPLE_WINDOWS = "multiple_windows"
    COPY_PASTE_ATTEMPT = "copy_paste_attempt"
    DEVELOPER_TOOLS = "developer_tools"
    NETWORK_DISCONNECT = "network_disconnect"
    UNSUPPORTED_BROWSER = "unsupported_browser"
    RIGHT_CLICK_ATTEMPT = "right_click_attempt"
    SCREENSHOT_ATTEMPT = "screenshot_attempt"
    TAB_SWITCH = "tab_switch"
    CAMERA_NOT_ACCESSIBLE = "camera_not_accessible"

# WebSocket manager for live camera monitoring
class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
    
    async def connect(self, websocket: WebSocket, exam_attempt_id: int):
        await websocket.accept()
        if exam_attempt_id not in self.active_connections:
            self.active_connections[exam_attempt_id] = []
        self.active_connections[exam_attempt_id].append(websocket)
        print(f"Examiner connected to monitor exam {exam_attempt_id}")
    
    def disconnect(self, websocket: WebSocket, exam_attempt_id: int):
        if exam_attempt_id in self.active_connections:
            self.active_connections[exam_attempt_id].remove(websocket)
            if not self.active_connections[exam_attempt_id]:
                del self.active_connections[exam_attempt_id]
    
    async def broadcast_to_examiners(self, exam_attempt_id: int, message: dict):
        if exam_attempt_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[exam_attempt_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.append(connection)
            
            # Remove disconnected clients
            for connection in disconnected:
                self.disconnect(connection, exam_attempt_id)

manager = ConnectionManager()

# WebSocket endpoint for live camera monitoring
@app.websocket("/ws/exam/{exam_attempt_id}/monitor")
async def websocket_monitor(websocket: WebSocket, exam_attempt_id: int):
    await manager.connect(websocket, exam_attempt_id)
    try:
        while True:
            # Keep connection alive - examiner just watches, doesn't send data
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, exam_attempt_id)

# Endpoint to receive camera frames for live monitoring
@app.post("/exam/{exam_attempt_id}/camera-frame")
async def receive_camera_frame(
    exam_attempt_id: int,
    frame_data: dict,
    db: Session = Depends(get_db)
):
    """Receive camera frames for live examiner monitoring - NO DATABASE STORAGE"""
    try:
        frame_base64 = frame_data.get("frame_data")
        timestamp = frame_data.get("timestamp")
        
        # Broadcast to all examiners watching this exam
        await manager.broadcast_to_examiners(exam_attempt_id, {
            "type": "camera_frame",
            "exam_attempt_id": exam_attempt_id,
            "frame_data": frame_base64,
            "timestamp": timestamp,
            "warning_count": frame_data.get("warning_count", 0)
        })
        
        return {
            "received": True,
            "message": "Frame sent to examiners",
            "examiners_count": len(manager.active_connections.get(exam_attempt_id, [])),
            # Note: Frame data is NOT stored in database
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing camera frame: {str(e)}")

# Endpoint for examiners to get active exams
@app.get("/admin/active-exams")
async def get_active_exams(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Get list of currently active exams for monitoring"""
    try:
        active_exams = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.status == 'in_progress'
        ).all()
        
        return {
            "active_exams": [
                {
                    "exam_attempt_id": exam.id,
                    "user_name": f"{exam.user.first_name} {exam.user.last_name}",
                    "started_at": exam.started_at,
                    "warning_count": getattr(exam, 'warning_count', 0)
                }
                for exam in active_exams
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching active exams: {str(e)}")

# Enhanced violation detection endpoint
# Enhanced violation detection endpoint - FIXED VERSION

@app.post("/exam/{exam_attempt_id}/detect-violation")
async def detect_violation(
    exam_attempt_id: int,
    violation_data: dict,
    db: Session = Depends(get_db)
):
    """Enhanced endpoint for all types of violations with camera warning logic"""
    try:
        violation_type = violation_data.get("type")
        details = violation_data.get("details", {})
        
        # Get current warning count BEFORE recording violation
        session = security_service.active_sessions.get(exam_attempt_id, {})
        current_warnings = session.get('warnings', 0)
        
        print(f"Current warnings before violation: {current_warnings}")
        print(f"Violation type: {violation_type}")
        
        # Special handling for camera not accessible
        if violation_type == "camera_not_accessible":
            camera_warning_given = security_service.camera_warnings_given.get(exam_attempt_id, False)
            
            if not camera_warning_given:
                # First camera warning - allow exam to continue
                security_service.camera_warnings_given[exam_attempt_id] = True
                
                # Record the violation but don't count it
                violation_record = models.ExamViolation(
                    exam_attempt_id=exam_attempt_id,
                    violation_type=violation_type,
                    details={**details, 'first_warning': True},
                    warning_count=current_warnings,  # Keep same count
                    created_at=datetime.utcnow()
                )
                db.add(violation_record)
                db.commit()
                
                # Notify examiners about camera warning
                await manager.broadcast_to_examiners(exam_attempt_id, {
                    "type": "camera_warning",
                    "exam_attempt_id": exam_attempt_id,
                    "violation_type": violation_type,
                    "warning_count": current_warnings,
                    "details": {**details, 'first_warning': True},
                    "timestamp": datetime.utcnow().isoformat(),
                    "message": "First camera warning - exam allowed to continue"
                })
                
                return {
                    "violation_recorded": True,
                    "violation_type": violation_type,
                    "warning_count": current_warnings,
                    "max_warnings": security_service.violation_threshold,
                    "auto_submitted": False,
                    "camera_warning": True,
                    "message": "Camera access required. Please allow camera access to continue. This is your first warning."
                }
        
        # For all other violations or subsequent camera violations
        should_auto_submit = await security_service.record_violation(
            exam_attempt_id, violation_type, details
        )
        
        # Get UPDATED warning count AFTER recording violation
        updated_session = security_service.active_sessions.get(exam_attempt_id, {})
        updated_warning_count = updated_session.get('warnings', 0)
        
        print(f"Updated warnings after violation: {updated_warning_count}")
        
        # Store violation in database for tracking
        violation_record = models.ExamViolation(
            exam_attempt_id=exam_attempt_id,
            violation_type=violation_type,
            details=details,
            warning_count=updated_warning_count,
            created_at=datetime.utcnow()
        )
        db.add(violation_record)
        db.commit()
        
        # Notify examiners about violation
        await manager.broadcast_to_examiners(exam_attempt_id, {
            "type": "violation",
            "exam_attempt_id": exam_attempt_id,
            "violation_type": violation_type,
            "warning_count": updated_warning_count,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        response = {
            "violation_recorded": True,
            "violation_type": violation_type,
            "warning_count": updated_warning_count,
            "max_warnings": security_service.violation_threshold,
            "auto_submitted": should_auto_submit,
            "camera_warning": False
        }
        
        if should_auto_submit:
            response["message"] = "Exam auto-submitted due to excessive violations"
            await auto_submit_exam_endpoint(exam_attempt_id, db)
        elif violation_type == "camera_not_accessible" and updated_warning_count > 0:
            response["message"] = f"Camera access required! Warnings: {updated_warning_count}/{security_service.violation_threshold}"
        else:
            response["message"] = f"Violation recorded. Warnings: {updated_warning_count}/{security_service.violation_threshold}"
        
        return response
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error recording violation: {str(e)}")


# Security middleware
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Middleware to block automation tools"""
    try:
        user_agent = request.headers.get("user-agent", "").lower()
        
        automation_indicators = [
            "selenium", "puppeteer", "playwright", 
            "headless", "phantomjs", "bot", "crawler"
        ]
        
        if any(indicator in user_agent for indicator in automation_indicators):
            return JSONResponse(
                status_code=403,
                content={"detail": "Automated access not allowed"}
            )
        
        response = await call_next(request)
        return response
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": "Security check failed"}
        )

@app.post("/mcq-exam/start-secure")
async def start_secure_exam(
    exam_data: schemas.MCQExamStart,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Start exam with security restrictions and camera warning logic"""
    try:
        # Browser validation
        user_agent = request.headers.get("user-agent", "").lower()
        allowed_browsers = ["chrome", "edg", "firefox", "safari"]
        
        if not any(browser in user_agent for browser in allowed_browsers):
            raise HTTPException(
                status_code=400, 
                detail="Unsupported browser. Please use Chrome, Edge, Firefox, or Safari."
            )
        
        # Start regular exam with dynamic questions and duration
        exam_response = start_mcq_exam(exam_data, db, current_user)
        
        # Initialize security session with camera warning tracking
        await security_service.start_proctoring_session(
            exam_response["exam_attempt_id"], 
            current_user.id
        )
        
        # Enhanced response with camera warning information
        exam_response.update({
            "proctoring_enabled": True,
            "security_measures": [
                "browser_monitoring",
                "tab_switching_detection", 
                "copy_paste_blocking",
                "developer_tools_blocking",
                "right_click_disabled"
            ],
            "camera_monitoring": "live_examiner_view",
            "camera_warning_policy": "First camera warning allowed - exam continues",
            "max_warnings": 3,
            "warning_count": 0,
            "auto_submit_on_violations": True
        })
        
        return exam_response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting secure exam: {str(e)}")


# Auto-submit endpoint
@app.post("/exam/{exam_attempt_id}/auto-submit")
async def auto_submit_exam_endpoint(
    exam_attempt_id: int,
    db: Session = Depends(get_db)
):
    """Auto-submit exam due to violations"""
    try:
        exam_attempt = db.query(models.MCQExamAttempt).filter(
            models.MCQExamAttempt.id == exam_attempt_id
        ).first()
        
        if not exam_attempt:
            raise HTTPException(status_code=404, detail="Exam attempt not found")
        
        if exam_attempt.status != 'in_progress':
            return {"message": "Exam already submitted", "status": exam_attempt.status}
        
        # Auto-submit with 0 score
        exam_attempt.status = 'auto_submitted'
        exam_attempt.completed_at = datetime.utcnow()
        exam_attempt.percentage_score = 0
        exam_attempt.correct_answers = 0
        exam_attempt.total_marks = 0
        exam_attempt.questions_attempted = 0
        
        assignment = exam_attempt.assignment
        if assignment:
            assignment.status = models.TrainingStatus.failed
            assignment.updated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "message": "Exam auto-submitted due to violations",
            "exam_attempt_id": exam_attempt_id,
            "status": "auto_submitted"
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error auto-submitting exam: {str(e)}")
#++++++++++
# ---------------- Exam Settings Endpoints ----------------
@app.get("/training-levels/{level_id}/exam-settings")
def get_exam_settings(
    level_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get exam settings for a specific training level"""
    try:
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == level_id
        ).first()
        
        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")
        
        # Return exam settings - using the same property names as frontend expects
        return {
            "level_id": training_level.id,
            "level_name": training_level.level_name,
            "exam_questions_count": getattr(training_level, 'exam_questions_count', 50),  # Default to 50
            "exam_duration_minutes": getattr(training_level, 'exam_duration_minutes', 20),  # Default to 20 minutes
            "pass_percentage": training_level.pass_percentage,
            "max_attempts": training_level.max_attempts
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching exam settings: {str(e)}")

@app.put("/training-levels/{level_id}/exam-settings")
def update_exam_settings(
    level_id: int,
    settings_data: schemas.ExamSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Update exam settings for a specific training level"""
    try:
        training_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.id == level_id
        ).first()
        
        if not training_level:
            raise HTTPException(status_code=404, detail="Training level not found")
        
        # Validate input
        exam_questions_count = settings_data.exam_questions_count
        exam_duration_minutes = settings_data.exam_duration_minutes
        
        if exam_questions_count is not None:
            try:
                exam_questions_count = int(exam_questions_count)
                if exam_questions_count <= 0 or exam_questions_count > 100:
                    raise HTTPException(
                        status_code=400, 
                        detail="Number of questions must be between 1 and 100"
                    )
            except ValueError:
                raise HTTPException(status_code=400, detail="Number of questions must be a valid integer")
        
        if exam_duration_minutes is not None:
            try:
                exam_duration_minutes = int(exam_duration_minutes)
                if exam_duration_minutes <= 0 or exam_duration_minutes > 180:
                    raise HTTPException(
                        status_code=400, 
                        detail="Exam duration must be between 1 and 180 minutes"
                    )
            except ValueError:
                raise HTTPException(status_code=400, detail="Exam duration must be a valid integer")
        
        # Update the training level with exam settings
        # Use the proper database columns that exist in the TrainingLevel model
        if exam_questions_count is not None:
            training_level.exam_questions_count = exam_questions_count
        
        if exam_duration_minutes is not None:
            training_level.exam_duration_minutes = exam_duration_minutes
        
        db.commit()
        db.refresh(training_level)
        
        return {
            "message": "Exam settings updated successfully",
            "level_id": level_id,
            "exam_questions_count": training_level.exam_questions_count,
            "exam_duration_minutes": training_level.exam_duration_minutes
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating exam settings: {str(e)}")

@app.get("/trainings/{training_id}/exam-settings")
def get_training_exam_settings(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get exam settings for all levels in a training"""
    try:
        training = db.query(models.Training).filter(models.Training.id == training_id).first()
        if not training:
            raise HTTPException(status_code=404, detail="Training not found")
        
        levels = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == training_id
        ).order_by(models.TrainingLevel.level_order.asc()).all()
        
        level_settings = []
        for level in levels:
            # Get exam settings from the correct database columns
            exam_questions_count = getattr(level, 'exam_questions_count', 50)
            exam_duration_minutes = getattr(level, 'exam_duration_minutes', 20)
            
            level_settings.append({
                "level_id": level.id,
                "level_name": level.level_name,
                "level_order": level.level_order,
                "exam_questions_count": exam_questions_count,
                "exam_duration_minutes": exam_duration_minutes,
                "pass_percentage": level.pass_percentage,
                "max_attempts": level.max_attempts
            })
        
        return {
            "training_id": training_id,
            "training_title": training.title,
            "levels": level_settings
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching training exam settings: {str(e)}")

@app.put("/trainings/{training_id}/exam-settings")
def update_training_exam_settings(
    training_id: int,
    levels_data: List[schemas.BulkExamSettingsUpdate],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Update exam settings for multiple levels in a training"""
    try:
        training = db.query(models.Training).filter(models.Training.id == training_id).first()
        if not training:
            raise HTTPException(status_code=404, detail="Training not found")
        
        updated_levels = []
        for level_data in levels_data:
            level_id = level_data.level_id
            exam_questions_count = level_data.exam_questions_count
            exam_duration_minutes = level_data.exam_duration_minutes
            
            level = db.query(models.TrainingLevel).filter(
                models.TrainingLevel.id == level_id,
                models.TrainingLevel.training_id == training_id
            ).first()
            
            if not level:
                continue
            
            # Validate and update
            if exam_questions_count is not None:
                level.exam_questions_count = exam_questions_count
            
            if exam_duration_minutes is not None:
                level.exam_duration_minutes = exam_duration_minutes
            
            updated_levels.append({
                "level_id": level.id,
                "level_name": level.level_name,
                "exam_questions_count": level.exam_questions_count,
                "exam_duration_minutes": level.exam_duration_minutes
            })
        
        db.commit()
        
        return {
            "message": "Training exam settings updated successfully",
            "training_id": training_id,
            "updated_levels": updated_levels
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating training exam settings: {str(e)}")
    
#++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
# ---------------- Employee Groups Endpoints ----------------
@app.post("/employee-groups", response_model=schemas.EmployeeGroupOut)
def create_employee_group(
    group: schemas.EmployeeGroupCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Create a new employee group based on projects with enhanced error handling"""
    try:
        print(f"Creating employee group: {group.dict()}")  # Debug log
        
        # Validate group name
        if not group.name or not group.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group name is required"
            )

        # Check if group name already exists
        existing_group = db.query(models.EmployeeGroup).filter(
            func.lower(models.EmployeeGroup.name) == func.lower(group.name.strip())
        ).first()
        
        if existing_group:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group with this name already exists"
            )

        # Create new group
        new_group = models.EmployeeGroup(
            name=group.name.strip(),
            description=group.description,
            project_name=group.project_name,
            created_by=current_user.id
        )
        
        db.add(new_group)
        db.flush()  # Get the ID without committing

        # Add employees to group
        members_list = []
        valid_employee_ids = []
        
        if group.employee_ids:
            # Validate all employees exist and are active
            employees = db.query(models.User).filter(
                models.User.id.in_(group.employee_ids),
                models.User.is_active == True,
                models.User.role == models.UserRole.employee
            ).all()
            
            valid_employee_ids = [emp.id for emp in employees]
            
            # Check for invalid employee IDs
            invalid_employee_ids = set(group.employee_ids) - set(valid_employee_ids)
            if invalid_employee_ids:
                print(f"Warning: Invalid employee IDs: {invalid_employee_ids}")

            for employee_id in valid_employee_ids:
                try:
                    group_member = models.GroupMember(
                        group_id=new_group.id,
                        employee_id=employee_id,
                        added_by=current_user.id
                    )
                    db.add(group_member)
                    members_list.append({
                        "employee_id": employee_id,
                        "group_member": group_member
                    })
                except Exception as e:
                    print(f"Error adding employee {employee_id} to group: {str(e)}")
                    continue

        db.commit()
        db.refresh(new_group)

        # Build members response
        members_out = []
        for member_data in members_list:
            member = member_data["group_member"]
            employee = db.query(models.User).filter(models.User.id == member_data["employee_id"]).first()
            if employee:
                members_out.append({
                    "id": member.id,
                    "employee_id": employee.id,
                    "employee_name": f"{employee.first_name} {employee.last_name}",
                    "employee_code": employee.user_code,
                    "department": employee.department,
                    "position": employee.position,
                    "email": employee.email,
                    "added_by": current_user.id,
                    "added_by_name": f"{current_user.first_name} {current_user.last_name}",
                    "added_at": member.added_at
                })

        return {
            "id": new_group.id,
            "group_id": new_group.id,
            "name": new_group.name,
            "description": new_group.description,
            "project_name": new_group.project_name,
            "created_by": new_group.created_by,
            "created_at": new_group.created_at,
            "updated_at": new_group.updated_at,
            "member_count": len(members_list),
            "creator_name": f"{current_user.first_name} {current_user.last_name}",
            "members": members_out
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Error creating employee group: {str(e)}")  # Debug log
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating employee group: {str(e)}"
        )

@app.get("/employee-groups/stats")
def get_employee_groups_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get statistics for employee groups"""
    try:
        total_groups = db.query(models.EmployeeGroup).count()
        
        # Count unique employees across all groups
        unique_employees = db.query(func.count(func.distinct(models.GroupMember.employee_id))).scalar() or 0
        
        # Count total trainings assigned to groups (placeholder - you might need to implement this)
        total_trainings = 0
        active_trainings = 0

        return {
            "total_groups": total_groups,
            "unique_employees": unique_employees,
            "total_trainings": total_trainings,
            "active_trainings": active_trainings
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching group statistics: {str(e)}"
        )# Add missing endpoint for group trainings list
@app.get("/group-trainings")
def get_group_trainings(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get group training assignments"""
    try:
        # This would typically join assignments with groups and trainings
        # For now, return a placeholder response
        return {
            "group_trainings": [],
            "total": 0,
            "skip": skip,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching group trainings: {str(e)}"
        )
@app.get("/group-trainings/group/{group_id}")
def get_group_trainings_by_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get trainings assigned to a specific group"""
    try:
        # Verify group exists
        group = db.query(models.EmployeeGroup).filter(
            models.EmployeeGroup.id == group_id
        ).first()
        
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Get assignments for group members
        group_assignments = (
            db.query(models.Assignment)
            .join(models.User, models.User.id == models.Assignment.user_id)
            .join(models.GroupMember, models.GroupMember.employee_id == models.User.id)
            .filter(
                models.GroupMember.group_id == group_id
            )
            .all()
        )
        
        # Organize by training
        training_data = {}
        for assignment in group_assignments:
            training_id = assignment.training_id
            if training_id not in training_data:
                training_data[training_id] = {
                    "training_id": training_id,
                    "training_title": assignment.training.title,
                    "assignments": []
                }
            
            training_data[training_id]["assignments"].append({
                "assignment_id": assignment.id,
                "employee_name": f"{assignment.user.first_name} {assignment.user.last_name}",
                "status": assignment.status,
                "current_level": assignment.current_level.level_name,
                "start_date": assignment.training_start_date,
                "end_date": assignment.training_end_date
            })
        
        return {
            "group_id": group_id,
            "group_name": group.name,
            "trainings": list(training_data.values())
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching group trainings: {str(e)}"
        )

@app.get("/group-trainings/group/{group_id}")
def get_group_trainings_by_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get trainings for a specific group (placeholder)"""
    return {
        "group_id": group_id,
        "group_name": "Group Placeholder",
        "trainings": [],
        "message": "Endpoint under development"
    }
# Add endpoint to get available employees for group creation
@app.get("/employees/available")
def get_available_employees(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get available employees for group assignment"""
    try:
        employees = db.query(models.User).filter(
            models.User.is_active == True,
            models.User.role == models.UserRole.employee
        ).offset(skip).limit(limit).all()

        result = []
        for employee in employees:
            result.append({
                "id": employee.id,
                "first_name": employee.first_name,
                "last_name": employee.last_name,
                "email": employee.email,
                "user_code": employee.user_code,
                "department": employee.department,
                "position": employee.position
            })

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching employees: {str(e)}"
        )



@app.get("/employee-groups", response_model=List[schemas.EmployeeGroupOut])
def list_employee_groups(
    skip: int = 0,
    limit: int = 100,
    project_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get all employee groups with member count and details"""
    try:
        query = db.query(models.EmployeeGroup)

        if project_name:
            query = query.filter(models.EmployeeGroup.project_name == project_name)

        groups = query.offset(skip).limit(limit).all()

        result = []
        for group in groups:
            # Get member count
            member_count = db.query(models.GroupMember).filter(
                models.GroupMember.group_id == group.id
            ).count()

            # Get group members with details
            members = db.query(models.GroupMember).filter(
                models.GroupMember.group_id == group.id
            ).all()
            
            members_out = []
            for member in members:
                members_out.append({
                    "id": member.id,
                    "employee_id": member.employee_id,
                    "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                    "employee_code": member.employee.user_code,
                    "department": member.employee.department,
                    "position": member.employee.position,
                    "email": member.employee.email,
                    "added_by": member.added_by,
                    "added_by_name": f"{member.added_by_user.first_name} {member.added_by_user.last_name}",
                    "added_at": member.added_at
                })

            result.append({
                "id": group.id,
                "group_id": group.id,
                "name": group.name,
                "description": group.description,
                "project_name": group.project_name,
                "created_by": group.created_by,
                "created_at": group.created_at,
                "updated_at": group.updated_at,
                "member_count": member_count,
                "creator_name": f"{group.creator.first_name} {group.creator.last_name}",
                "members": members_out
            })

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching employee groups: {str(e)}"
        )



@app.get("/employee-groups/{group_id}", response_model=schemas.EmployeeGroupOut)
def get_employee_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get specific employee group with details"""
    try:
        group = db.query(models.EmployeeGroup).filter(
            models.EmployeeGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee group not found"
            )

        # Get member count
        member_count = db.query(models.GroupMember).filter(
            models.GroupMember.group_id == group.id
        ).count()

        # Get group members with details
        members = db.query(models.GroupMember).filter(
            models.GroupMember.group_id == group.id
        ).all()
        
        members_out = []
        for member in members:
            members_out.append({
                "id": member.id,
                "employee_id": member.employee_id,
                "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                "employee_code": member.employee.user_code,
                "department": member.employee.department,
                "position": member.employee.position,
                "email": member.employee.email,
                "added_by": member.added_by,
                "added_by_name": f"{member.added_by_user.first_name} {member.added_by_user.last_name}",
                "added_at": member.added_at
            })

        return {
            "id": group.id,
            "group_id": group.id,
            "name": group.name,
            "description": group.description,
            "project_name": group.project_name,
            "created_by": group.created_by,
            "created_at": group.created_at,
            "updated_at": group.updated_at,
            "member_count": member_count,
            "creator_name": f"{group.creator.first_name} {group.creator.last_name}",
            "members": members_out
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching employee group: {str(e)}"
        )



@app.put("/employee-groups/{group_id}", response_model=schemas.EmployeeGroupOut)
def update_employee_group(
    group_id: int,
    group_update: schemas.EmployeeGroupUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Update employee group details and members"""
    try:
        group = db.query(models.EmployeeGroup).filter(
            models.EmployeeGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee group not found"
            )

        update_data = group_update.dict(exclude_unset=True)
        employee_ids = update_data.pop('employee_ids', None)

        # Update basic group info
        for key, value in update_data.items():
            if value is not None:
                setattr(group, key, value)

        group.updated_at = datetime.utcnow()

        # Update group members if provided
        if employee_ids is not None:
            # Remove existing members
            db.query(models.GroupMember).filter(
                models.GroupMember.group_id == group_id
            ).delete()

            # Add new members
            for employee_id in employee_ids:
                # Verify employee exists and is active
                employee = db.query(models.User).filter(
                    models.User.id == employee_id,
                    models.User.is_active == True,
                    models.User.role == models.UserRole.employee
                ).first()
                
                if employee:
                    group_member = models.GroupMember(
                        group_id=group_id,
                        employee_id=employee_id,
                        added_by=current_user.id
                    )
                    db.add(group_member)

        db.commit()
        db.refresh(group)

        # Get updated member count
        member_count = db.query(models.GroupMember).filter(
            models.GroupMember.group_id == group_id
        ).count()

        # Get updated group members for response
        members = db.query(models.GroupMember).filter(
            models.GroupMember.group_id == group_id
        ).all()
        
        members_out = []
        for member in members:
            members_out.append({
                "id": member.id,
                "employee_id": member.employee_id,
                "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                "employee_code": member.employee.user_code,
                "department": member.employee.department,
                "position": member.employee.position,
                "email": member.employee.email,
                "added_by": member.added_by,
                "added_by_name": f"{member.added_by_user.first_name} {member.added_by_user.last_name}",
                "added_at": member.added_at
            })

        return {
            "id": group.id,
            "group_id": group.id,
            "name": group.name,
            "description": group.description,
            "project_name": group.project_name,
            "created_by": group.created_by,
            "created_at": group.created_at,
            "updated_at": group.updated_at,
            "member_count": member_count,
            "creator_name": f"{group.creator.first_name} {group.creator.last_name}",
            "members": members_out
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating employee group: {str(e)}"
        )



@app.delete("/employee-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Delete an employee group"""
    try:
        group = db.query(models.EmployeeGroup).filter(
            models.EmployeeGroup.id == group_id
        ).first()

        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee group not found"
            )

        # Delete group members first
        db.query(models.GroupMember).filter(
            models.GroupMember.group_id == group_id
        ).delete()

        # Delete the group
        db.delete(group)
        db.commit()

        return None

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting employee group: {str(e)}"
        )

# ---------------- Group Training Assignment Endpoints ----------------

@app.post("/group-trainings/assign")
def assign_training_to_group(
    assignment_data: schemas.GroupTrainingAssign,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Assign training to entire employee group"""
    try:
        # Verify group exists
        group = db.query(models.EmployeeGroup).filter(
            models.EmployeeGroup.id == assignment_data.group_id
        ).first()

        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Employee group not found"
            )

        # Verify training exists
        training = db.query(models.Training).filter(
            models.Training.id == assignment_data.training_id
        ).first()

        if not training:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Training not found"
            )

        # Get group members
        group_members = db.query(models.GroupMember).filter(
            models.GroupMember.group_id == assignment_data.group_id
        ).all()

        if not group_members:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Employee group has no members"
            )

        created_assignments = []
        failed_assignments = []

        # Get the first level for this training
        first_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == assignment_data.training_id
        ).order_by(models.TrainingLevel.level_order.asc()).first()

        if not first_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No levels found for this training"
            )

        for member in group_members:
            try:
                # Check if employee already has this training assigned
                existing_assignment = db.query(models.Assignment).filter(
                    models.Assignment.user_id == member.employee_id,
                    models.Assignment.training_id == assignment_data.training_id
                ).first()

                if existing_assignment:
                    failed_assignments.append({
                        "employee_id": member.employee_id,
                        "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                        "reason": "Training already assigned"
                    })
                    continue

                # Create new assignment
                new_assignment = models.Assignment(
                    user_id=member.employee_id,
                    training_id=assignment_data.training_id,
                    current_level_id=first_level.id,
                    assigned_by=current_user.id,
                    group_id=assignment_data.group_id,
                    training_start_date=assignment_data.training_start_date,
                    training_end_date=assignment_data.training_end_date,
                    status=models.TrainingStatus.assigned
                )

                db.add(new_assignment)
                db.flush()

                # Create level dates if provided
                if assignment_data.level_dates:
                    for level_id_str, dates in assignment_data.level_dates.items():
                        try:
                            level_id = int(level_id_str)
                            level_exists = db.query(models.TrainingLevel).filter(
                                models.TrainingLevel.id == level_id,
                                models.TrainingLevel.training_id == assignment_data.training_id
                            ).first()
                            
                            if level_exists:
                                level_date = models.AssignmentLevelDate(
                                    assignment_id=new_assignment.id,
                                    level_id=level_id,
                                    start_date=dates.get('start_date'),
                                    due_date=dates.get('due_date')
                                )
                                db.add(level_date)
                        except (ValueError, TypeError):
                            continue

                created_assignments.append({
                    "employee_id": member.employee_id,
                    "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                    "assignment_id": new_assignment.id
                })

                # Send email notification
                try:
                    email_service.send_training_assignment_email(
                        employee_email=member.employee.email,
                        employee_name=f"{member.employee.first_name} {member.employee.last_name}",
                        training_title=training.title,
                        training_description=training.description or "No description available",
                        category=training.category,
                        current_level=first_level.level_name,
                        level_description=first_level.description or "No description available",
                        duration_hours=first_level.duration_hours or 0,
                        prerequisites=first_level.prerequisites or "None",
                        learning_objectives=first_level.learning_objectives or "Not specified",
                        training_start_date=assignment_data.training_start_date.strftime("%Y-%m-%d") if assignment_data.training_start_date else "Not specified",
                        training_end_date=assignment_data.training_end_date.strftime("%Y-%m-%d") if assignment_data.training_end_date else "Not specified"
                    )
                except Exception as email_error:
                    print(f"Failed to send assignment email: {str(email_error)}")

            except Exception as e:
                failed_assignments.append({
                    "employee_id": member.employee_id,
                    "employee_name": f"{member.employee.first_name} {member.employee.last_name}",
                    "reason": str(e)
                })
                continue

        db.commit()

        return {
            "message": f"Training assigned to {len(created_assignments)} employees in group",
            "group_id": assignment_data.group_id,
            "group_name": group.name,
            "training_id": assignment_data.training_id,
            "training_title": training.title,
            "created_assignments": created_assignments,
            "failed_assignments": failed_assignments,
            "total_attempted": len(group_members),
            "successful": len(created_assignments),
            "failed": len(failed_assignments)
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error assigning training to group: {str(e)}"
        )

# ---------------- Self-Assignment Endpoints ----------------

@app.post("/trainings/self-assign")
def self_assign_training(
    training_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Allow employees to assign trainings to themselves"""
    try:
        # Verify training exists
        training = db.query(models.Training).filter(
            models.Training.id == training_id
        ).first()

        if not training:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Training not found"
            )

        # Check if user already has this training assigned
        existing_assignment = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id,
            models.Assignment.training_id == training_id
        ).first()

        if existing_assignment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have this training assigned"
            )

        # Get the first level for this training
        first_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == training_id
        ).order_by(models.TrainingLevel.level_order.asc()).first()

        if not first_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No levels found for this training"
            )

        # Create new assignment
        new_assignment = models.Assignment(
            user_id=current_user.id,
            training_id=training_id,
            current_level_id=first_level.id,
            assigned_by=current_user.id,  # Self-assigned
            status=models.TrainingStatus.assigned,
            training_start_date=datetime.utcnow().date(),
            training_end_date=datetime.utcnow().date() + timedelta(days=30)  # Default 30 days
        )

        db.add(new_assignment)
        db.commit()
        db.refresh(new_assignment)

        return {
            "message": "Training assigned successfully",
            "assignment_id": new_assignment.id,
            "training_title": training.title,
            "current_level": first_level.level_name,
            "start_date": new_assignment.training_start_date,
            "end_date": new_assignment.training_end_date
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error self-assigning training: {str(e)}"
        )



# ---------------- Enhanced Training Search and Filters ----------------

@app.get("/trainings/search/filter")
def search_trainings_with_filters(
    title: Optional[str] = None,
    category: Optional[str] = None,
    has_levels: Optional[bool] = None,
    min_duration: Optional[int] = None,
    max_duration: Optional[int] = None,
    duration_unit: str = "hours",  # "hours" or "minutes"
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Search trainings with advanced filters"""
    try:
        query = db.query(models.Training)

        # Title search (case-insensitive partial match)
        if title:
            query = query.filter(models.Training.title.ilike(f"%{title}%"))

        # Category filter
        if category:
            query = query.filter(models.Training.category == category)

        # Has levels filter
        if has_levels is not None:
            query = query.filter(models.Training.has_levels == has_levels)

        # Duration filters
        if min_duration is not None or max_duration is not None:
            # Join with TrainingLevel to filter by duration
            query = query.join(models.TrainingLevel)
            
            if duration_unit == "minutes":
                duration_field = models.TrainingLevel.duration_minutes
            else:
                duration_field = models.TrainingLevel.duration_hours

            if min_duration is not None:
                query = query.filter(duration_field >= min_duration)
            if max_duration is not None:
                query = query.filter(duration_field <= max_duration)

        # Date filters
        if created_after:
            try:
                created_after_date = datetime.strptime(created_after, "%Y-%m-%d")
                query = query.filter(models.Training.created_at >= created_after_date)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid created_after date format. Use YYYY-MM-DD"
                )

        if created_before:
            try:
                created_before_date = datetime.strptime(created_before, "%Y-%m-%d")
                query = query.filter(models.Training.created_at <= created_before_date)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid created_before date format. Use YYYY-MM-DD"
                )

        trainings = query.distinct().offset(skip).all()

        # Format response with enhanced information
        result = []
        for training in trainings:
            total_duration_minutes = 0
            total_duration_hours = 0
            
            for level in training.levels:
                total_duration_minutes += level.duration_minutes or 0
                total_duration_hours += level.duration_hours or 0

            training_dict = {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
                "has_levels": training.has_levels,
                "total_duration_minutes": total_duration_minutes,
                "total_duration_hours": total_duration_hours,
                "total_levels": len(training.levels),
                "created_at": training.created_at,
                "creator_name": f"{training.creator.first_name} {training.creator.last_name}",
                "levels": [
                    {
                        "id": level.id,
                        "level_name": level.level_name,
                        "level_order": level.level_order,
                        "duration_minutes": level.duration_minutes,
                        "duration_hours": level.duration_hours,
                        "pass_percentage": level.pass_percentage
                    }
                    for level in training.levels
                ]
            }
            result.append(training_dict)

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching trainings: {str(e)}"
        )

# ---------------- Bulk Operations Endpoints ----------------

@app.post("/assignments/bulk-operations")
def bulk_assignments_operations(
    operations: schemas.BulkAssignmentsOperations,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin])),
):
    """Perform bulk operations on assignments"""
    try:
        results = {
            "updated": [],
            "failed": [],
            "total_processed": 0,
            "successful": 0
        }

        for assignment_id in operations.assignment_ids:
            try:
                assignment = db.query(models.Assignment).filter(
                    models.Assignment.id == assignment_id
                ).first()

                if not assignment:
                    results["failed"].append({
                        "assignment_id": assignment_id,
                        "reason": "Assignment not found"
                    })
                    continue

                # Perform the requested operation
                if operations.operation == "delete":
                    # Delete evaluations first
                    db.query(models.Evaluation).filter(
                        models.Evaluation.assignment_id == assignment_id
                    ).delete()
                    
                    # Delete assignment
                    db.delete(assignment)
                    action = "deleted"

                elif operations.operation == "reset":
                    # Reset to first level
                    first_level = db.query(models.TrainingLevel).filter(
                        models.TrainingLevel.training_id == assignment.training_id
                    ).order_by(models.TrainingLevel.level_order.asc()).first()
                    
                    if first_level:
                        assignment.current_level_id = first_level.id
                        assignment.status = models.TrainingStatus.assigned
                        assignment.actual_completion_date = None
                        
                        # Delete evaluations
                        db.query(models.Evaluation).filter(
                            models.Evaluation.assignment_id == assignment_id
                        ).delete()
                        
                        action = "reset"
                    else:
                        results["failed"].append({
                            "assignment_id": assignment_id,
                            "reason": "No levels found for this training"
                        })
                        continue

                elif operations.operation == "update_status":
                    if operations.new_status:
                        assignment.status = models.TrainingStatus(operations.new_status)
                        if operations.new_status == "completed":
                            assignment.actual_completion_date = datetime.utcnow()
                        action = f"status updated to {operations.new_status}"
                    else:
                        results["failed"].append({
                            "assignment_id": assignment_id,
                            "reason": "No new status provided"
                        })
                        continue

                else:
                    results["failed"].append({
                        "assignment_id": assignment_id,
                        "reason": f"Unsupported operation: {operations.operation}"
                    })
                    continue

                assignment.updated_by = current_user.id
                assignment.updated_at = datetime.utcnow()

                results["updated"].append({
                    "assignment_id": assignment_id,
                    "employee_name": f"{assignment.user.first_name} {assignment.user.last_name}",
                    "training_title": assignment.training.title,
                    "action": action
                })
                results["successful"] += 1

            except Exception as e:
                results["failed"].append({
                    "assignment_id": assignment_id,
                    "reason": str(e)
                })
                continue

            results["total_processed"] += 1

        db.commit()

        return {
            "message": f"Bulk operation completed: {results['successful']} successful, {len(results['failed'])} failed",
            "results": results
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error performing bulk operations: {str(e)}"
        )

# ---------------- Training Completion Endpoints ----------------

@app.put("/assignments/{assignment_id}/mark-completed")
def mark_assignment_completed(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Mark assignment as completed with validation"""
    try:
        assignment = db.query(models.Assignment).filter(
            models.Assignment.id == assignment_id
        ).first()

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Check permissions
        if current_user.role != models.UserRole.admin and assignment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized for this assignment")

        # Check if assignment can be marked as completed
        is_eligible, reason = can_mark_as_completed(assignment, db)
        
        if not is_eligible:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark as completed: {reason}"
            )

        # Update assignment status
        assignment.status = models.TrainingStatus.completed
        assignment.actual_completion_date = datetime.utcnow()
        assignment.updated_by = current_user.id
        assignment.updated_at = datetime.utcnow()

        db.commit()

        return {
            "message": "Assignment marked as completed successfully",
            "assignment_id": assignment_id,
            "completed_at": assignment.actual_completion_date,
            "employee_name": f"{assignment.user.first_name} {assignment.user.last_name}",
            "training_title": assignment.training.title
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error marking assignment as completed: {str(e)}"
        )

#++++++
@app.post("/trainings/self-assign-any")
def self_assign_any_training(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Allow employees to auto-assign any available training they don't have"""
    try:
        # Get all trainings (removed is_active filter since the field doesn't exist)
        all_trainings = db.query(models.Training).all()
        
        if not all_trainings:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No trainings available"
            )

        # Get trainings the user already has assigned
        existing_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        ).all()
        
        assigned_training_ids = [assignment.training_id for assignment in existing_assignments]
        
        # Find the first training not already assigned
        available_training = None
        for training in all_trainings:
            if training.id not in assigned_training_ids:
                available_training = training
                break
        
        if not available_training:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have all available trainings assigned"
            )

        # Get the first level for this training
        first_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == available_training.id
        ).order_by(models.TrainingLevel.level_order.asc()).first()

        if not first_level:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No levels found for this training"
            )

        # Create new assignment
        new_assignment = models.Assignment(
            user_id=current_user.id,
            training_id=available_training.id,
            current_level_id=first_level.id,
            assigned_by=current_user.id,  # Self-assigned
            status=models.TrainingStatus.assigned,
            training_start_date=datetime.utcnow().date(),
            training_end_date=datetime.utcnow().date() + timedelta(days=30)  # Default 30 days
        )

        db.add(new_assignment)
        db.commit()
        db.refresh(new_assignment)

        # Send email notification
        try:
            email_service.send_training_assignment_email(
                employee_email=current_user.email,
                employee_name=f"{current_user.first_name} {current_user.last_name}",
                training_title=available_training.title,
                training_description=available_training.description or "No description available",
                category=available_training.category,
                current_level=first_level.level_name,
                level_description=first_level.description or "No description available",
                duration_hours=first_level.duration_hours or 0,
                prerequisites=first_level.prerequisites or "None",
                learning_objectives=first_level.learning_objectives or "Not specified",
                training_start_date=new_assignment.training_start_date.strftime("%Y-%m-%d"),
                training_end_date=new_assignment.training_end_date.strftime("%Y-%m-%d")
            )
        except Exception as email_error:
            print(f"Failed to send assignment email: {str(email_error)}")

        return {
            "message": f"Training '{available_training.title}' assigned successfully",
            "assignment_id": new_assignment.id,
            "training_title": available_training.title,
            "current_level": first_level.level_name,
            "start_date": new_assignment.training_start_date,
            "end_date": new_assignment.training_end_date
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error self-assigning training: {str(e)}"
        )
    
#++++++++

@app.get("/trainings/available-for-self-assign")
def get_available_trainings_for_self_assign(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get trainings available for self-assignment (trainings the user doesn't already have)"""
    try:
        # Get all trainings
        all_trainings = db.query(models.Training).filter(
            models.Training.is_active == True
        ).all()
        
        # Get trainings the user already has assigned
        existing_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        ).all()
        
        assigned_training_ids = [assignment.training_id for assignment in existing_assignments]
        
        # Filter out trainings the user already has
        available_trainings = [
            training for training in all_trainings 
            if training.id not in assigned_training_ids
        ]
        
        # Format response
        result = []
        for training in available_trainings:
            # Calculate total duration
            total_duration_hours = 0
            total_duration_minutes = 0
            
            for level in training.levels:
                total_duration_hours += level.duration_hours or 0
                total_duration_minutes += level.duration_minutes or 0
            
            training_dict = {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
                "has_levels": training.has_levels,
                "duration_hours": total_duration_hours,
                "duration_minutes": total_duration_minutes,
                "levels_count": len(training.levels),
                "created_at": training.created_at,
                "creator_name": f"{training.creator.first_name} {training.creator.last_name}",
            }
            result.append(training_dict)
        
        # Always return 200 with array, even if empty
        return result
        
    except Exception as e:
        # Log error but still return empty array
        print(f"Error fetching available trainings: {str(e)}")
        return []
#++++
@app.get("/trainings/available-for-self-assign")
def get_available_trainings_for_self_assign(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get trainings available for self-assignment (trainings the user doesn't already have)"""
    try:
        # Get all trainings
        all_trainings = db.query(models.Training).filter(
            models.Training.is_active == True
        ).all()
        
        # Get trainings the user already has assigned
        user_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        ).all()
        
        assigned_training_ids = [assignment.training_id for assignment in user_assignments]
        
        # Filter out trainings the user already has
        available_trainings = [
            training for training in all_trainings 
            if training.id not in assigned_training_ids
        ]
        
        # Format response
        result = []
        for training in available_trainings:
            # Calculate total duration
            total_duration_hours = 0
            total_duration_minutes = 0
            
            for level in training.levels:
                total_duration_hours += level.duration_hours or 0
                total_duration_minutes += level.duration_minutes or 0
            
            training_dict = {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
                "has_levels": training.has_levels,
                "duration_hours": total_duration_hours,
                "duration_minutes": total_duration_minutes,
                "levels_count": len(training.levels),
                "created_at": training.created_at,
                "creator_name": f"{training.creator.first_name} {training.creator.last_name}",
            }
            result.append(training_dict)
        
        # Always return 200 with array, even if empty
        return result
        
    except Exception as e:
        # Log error but still return empty array
        print(f"Error fetching available trainings: {str(e)}")
        return []
#++++++
# Add these endpoints to your backend (main.py)

@app.get("/trainings/available-for-self-assign")
def get_trainings_for_self_assign(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get trainings available for self-assignment"""
    try:
        # Get all trainings (filter out inactive ones if you have an is_active field)
        all_trainings = db.query(models.Training).all()
        
        # Get trainings the user already has assigned
        existing_assignments = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id
        ).all()
        
        assigned_training_ids = [assignment.training_id for assignment in existing_assignments]
        
        # Filter out trainings the user already has
        available_trainings = [
            training for training in all_trainings 
            if training.id not in assigned_training_ids
        ]
        
        # Format response
        result = []
        for training in available_trainings:
            # Calculate total duration
            total_duration_hours = 0
            total_duration_minutes = 0
            
            for level in training.levels:
                total_duration_hours += level.duration_hours or 0
                total_duration_minutes += level.duration_minutes or 0
            
            training_dict = {
                "id": training.id,
                "title": training.title,
                "description": training.description,
                "category": training.category,
                "has_levels": training.has_levels,
                "duration_hours": total_duration_hours,
                "duration_minutes": total_duration_minutes,
                "levels_count": len(training.levels),
                "created_at": training.created_at,
                "creator_name": f"{training.creator.first_name} {training.creator.last_name}",
            }
            result.append(training_dict)
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching available trainings: {str(e)}"
        )

@app.post("/trainings/self-assign")
def self_assign_training_specific(
    assignment_data: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Allow employees to assign specific training to themselves"""
    try:
        training_id = assignment_data.get("training_id")
        
        if not training_id:
            raise HTTPException(status_code=400, detail="Training ID is required")

        # Verify training exists
        training = db.query(models.Training).filter(
            models.Training.id == training_id
        ).first()

        if not training:
            raise HTTPException(status_code=404, detail="Training not found")

        # Check if user already has this training assigned
        existing_assignment = db.query(models.Assignment).filter(
            models.Assignment.user_id == current_user.id,
            models.Assignment.training_id == training_id
        ).first()

        if existing_assignment:
            raise HTTPException(
                status_code=400,
                detail="You already have this training assigned"
            )

        # Get the first level for this training
        first_level = db.query(models.TrainingLevel).filter(
            models.TrainingLevel.training_id == training_id
        ).order_by(models.TrainingLevel.level_order.asc()).first()

        if not first_level:
            raise HTTPException(
                status_code=404,
                detail="No levels found for this training"
            )

        # Create new assignment
        new_assignment = models.Assignment(
            user_id=current_user.id,
            training_id=training_id,
            current_level_id=first_level.id,
            assigned_by=current_user.id,  # Self-assigned
            status=models.TrainingStatus.assigned,
            training_start_date=datetime.utcnow().date(),
            training_end_date=datetime.utcnow().date() + timedelta(days=30)  # Default 30 days
        )

        db.add(new_assignment)
        db.commit()
        db.refresh(new_assignment)

        # Send email notification
        try:
            email_service.send_training_assignment_email(
                employee_email=current_user.email,
                employee_name=f"{current_user.first_name} {current_user.last_name}",
                training_title=training.title,
                training_description=training.description or "No description available",
                category=training.category,
                current_level=first_level.level_name,
                level_description=first_level.description or "No description available",
                duration_hours=first_level.duration_hours or 0,
                prerequisites=first_level.prerequisites or "None",
                learning_objectives=first_level.learning_objectives or "Not specified",
                training_start_date=new_assignment.training_start_date.strftime("%Y-%m-%d"),
                training_end_date=new_assignment.training_end_date.strftime("%Y-%m-%d")
            )
        except Exception as email_error:
            print(f"Failed to send assignment email: {str(email_error)}")

        return {
            "message": f"Training '{training.title}' assigned successfully",
            "assignment_id": new_assignment.id,
            "training_title": training.title
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error self-assigning training: {str(e)}"
        )
# ---------------- Employee Group Endpoints ----------------
@app.post("/groups", response_model=schemas.EmployeeGroupOut)
def create_group(
    group_data: schemas.EmployeeGroupCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Create a new employee group"""
    try:
        # Check if group name exists
        existing = db.query(models.EmployeeGroup).filter(models.EmployeeGroup.name == group_data.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Group name already exists")
            
        new_group = models.EmployeeGroup(
            name=group_data.name,
            description=group_data.description,
            project_name=group_data.project_name,
            created_by=current_user.id
        )
        db.add(new_group)
        db.flush()
        
        # Add members
        if group_data.employee_ids:
            for emp_id in group_data.employee_ids:
                member = models.GroupMember(
                    group_id=new_group.id,
                    employee_id=emp_id,
                    added_by=current_user.id
                )
                db.add(member)
        
        db.commit()
        db.refresh(new_group)
        
        # Format response
        return {
            "id": new_group.id,
            "group_id": new_group.id,
            "name": new_group.name,
            "description": new_group.description,
            "project_name": new_group.project_name,
            "created_by": new_group.created_by,
            "created_at": new_group.created_at,
            "updated_at": new_group.updated_at,
            "member_count": len(new_group.members),
            "creator_name": f"{current_user.first_name} {current_user.last_name}",
            "members": [{"id": m.employee.id, "name": m.employee.full_name} for m in new_group.members if m.employee]
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating group: {str(e)}")

@app.get("/groups", response_model=List[schemas.EmployeeGroupOut])
def list_groups(
    skip: int = 0,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """List all employee groups"""
    try:
        groups = db.query(models.EmployeeGroup).offset(skip).all()
        result = []
        for g in groups:
            result.append({
                "id": g.id,
                "group_id": g.id,
                "name": g.name,
                "description": g.description,
                "project_name": g.project_name,
                "created_by": g.created_by,
                "created_at": g.created_at,
                "updated_at": g.updated_at,
                "member_count": len(g.members),
                "creator_name": f"{g.creator.first_name} {g.creator.last_name}" if g.creator else "Unknown",
                "members": [{"id": m.employee.id, "name": m.employee.full_name} for m in g.members if m.employee]
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching groups: {str(e)}")

@app.get("/groups/{group_id}", response_model=schemas.EmployeeGroupOut)
def get_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Get details of a specific group"""
    try:
        g = db.query(models.EmployeeGroup).filter(models.EmployeeGroup.id == group_id).first()
        if not g:
            raise HTTPException(status_code=404, detail="Group not found")
            
        return {
            "id": g.id,
            "group_id": g.id,
            "name": g.name,
            "description": g.description,
            "project_name": g.project_name,
            "created_by": g.created_by,
            "created_at": g.created_at,
            "updated_at": g.updated_at,
            "member_count": len(g.members),
            "creator_name": f"{g.creator.first_name} {g.creator.last_name}" if g.creator else "Unknown",
            "members": [{"id": m.employee.id, "name": m.employee.full_name} for m in g.members if m.employee]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching group: {str(e)}")

@app.delete("/groups/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role([models.UserRole.admin]))
):
    """Delete an employee group"""
    try:
        g = db.query(models.EmployeeGroup).filter(models.EmployeeGroup.id == group_id).first()
        if not g:
            raise HTTPException(status_code=404, detail="Group not found")
            
        db.delete(g)
        db.commit()
        return None
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting group: {str(e)}")
#+++++++++++++++++++

# ---------------- In-App Notification Endpoints ----------------
@app.post("/notifications/", response_model=schemas.NotificationOut)
def create_notification(notification: schemas.NotificationCreate, db: Session = Depends(get_db)):
    try:
        # Accept either numeric user_id or user_code (e.g., AA-1234)
        user = None
        user_id_str = str(notification.user_id)
        if user_id_str.isdigit():
            user = db.query(models.User).filter(models.User.id == int(notification.user_id)).first()
        else:
            # Accept both AA-1234 and AASPL-1234
            user = db.query(models.User).filter(
                (models.User.user_code == user_id_str) |
                (models.User.user_code == f"AASPL-{user_id_str}" if not user_id_str.startswith("AASPL-") else user_id_str)
            ).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User with id or code {notification.user_id} not found")

        db_notification = models.Notification(
            user_id=user.id,
            title=notification.title,
            message=notification.message
        )
        db.add(db_notification)
        db.commit()
        db.refresh(db_notification)
        return db_notification
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Notification creation error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create notification: {e}")

@app.get("/notifications/{user_id}", response_model=List[schemas.NotificationOut])
def get_notifications(user_id: int, db: Session = Depends(get_db)):
    notifications = db.query(models.Notification).filter(models.Notification.user_id == user_id).order_by(models.Notification.created_at.desc()).all()
    return notifications

@app.put("/notifications/{notification_id}/read", response_model=schemas.NotificationOut)
def mark_notification_read(notification_id: int, db: Session = Depends(get_db)):
    notification = db.query(models.Notification).filter(models.Notification.id == notification_id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    db.commit()
    db.refresh(notification)
    return notification

# ---------------- Health check & root ------------------------
@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.get("/")
def read_root():
    return {
        "message": "Employee Training Tracker API",
        "version": "1.0.0",
        "documentation": "/docs",
    }
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


