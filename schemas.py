from pydantic import BaseModel, EmailStr, validator, field_validator,Field
from typing import Optional, List,Dict,Any,Union
from datetime import datetime,date,time
from enum import Enum
import re


# ---------------- Enums ----------------
class UserRole(str, Enum):
    admin = "admin"
    employee = "employee"

class TrainingStatus(str, Enum):
    assigned = "assigned"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"

class TrainingLevelEnum(str, Enum):
    basic = "basic"
    intermediate = "intermediate"
    advanced = "advanced"

class EvaluationStatus(str, Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    passed = "passed"
    failed = "failed"

# ---------------- User Schemas ----------------
class UserBase(BaseModel):
    user_code: str
    first_name: str
    last_name: str
    username: str
    email: EmailStr
    designation: Optional[str] = None
    department: str
    position: Optional[str] = None
    role: UserRole = UserRole.employee

    @field_validator('user_code')
    @classmethod
    def validate_user_code(cls, v):
        if not v.startswith('AASPL-'):
            v = f'AASPL-{v}'
        return v

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_.]+$', v):
            raise ValueError('Username can only contain letters, numbers, underscores, and dots')
        return v

class UserCreate(UserBase):
    password: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('Password must be at least 6 characters long')
        return v

    @field_validator('user_code', mode='before')
    @classmethod
    def validate_user_code_before(cls, v):
        if v and not v.startswith('AASPL-'):
            return f'AASPL-{v}'
        return v

class UserUpdate(BaseModel):
    user_code: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    role: Optional[UserRole] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserOut(UserBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username_or_email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# ---------------- Training Level Schemas ----------------
class TrainingLevelBase(BaseModel):
    level_name: str
    level_order: int
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    duration_hours: Optional[int] = None
    prerequisites: Optional[str] = None
    learning_objectives: Optional[str] = None
    learning_plan_links: Optional[str] = None
    learning_materials: Optional[str] = None
    pass_percentage: float = 60.0
    max_attempts: int = 3
    exam_questions_count: int = 50
    exam_duration_minutes: int = 25

class TrainingLevelCreate(TrainingLevelBase):
    pass

class TrainingLevelUpdate(BaseModel):
    level_name: Optional[str] = None
    description: Optional[str] = None
    duration_hours: Optional[int] = None
    prerequisites: Optional[str] = None
    learning_objectives: Optional[str] = None
    learning_plan_links: Optional[str] = None
    learning_materials: Optional[str] = None
    pass_percentage: Optional[float] = None
    max_attempts: Optional[int] = None
    exam_questions_count: Optional[int] = None
    exam_duration_minutes: Optional[int] = None

class TrainingLevelOut(TrainingLevelBase):
    id: int
    training_id: int
    created_at: datetime

    class Config:
        from_attributes = True

# ---------------- Training Schemas ----------------
class TrainingBase(BaseModel):
    title: str
    description: Optional[str] = None
    category: str

class TrainingCreate(TrainingBase):
    levels: List[TrainingLevelCreate]

class TrainingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None

class TrainingOut(TrainingBase):
    id: int
    created_by: int
    updated_by: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    levels: List[TrainingLevelOut] = []
    creator_name: Optional[str] = None
    updater_name: Optional[str] = None

    class Config:
        orm_mode = True

# ---------------- Assignment Schemas ----------------
class AssignmentBase(BaseModel):
    training_id: int
    current_level_id: int
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    status: TrainingStatus = TrainingStatus.assigned
    group_id: Optional[int] = None
#++++++++

#++++++++++
class AssignmentCreate(AssignmentBase):
    user_id: int

class AssignmentUpdate(BaseModel):
    status: Optional[TrainingStatus] = None
    current_level_id: Optional[int] = None
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    actual_completion_date: Optional[datetime] = None
    group_id: Optional[int] = None

class AssignmentOut(AssignmentBase):
    id: int
    user_id: int
    training_id: int
    current_level_id: int
    status: TrainingStatus
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    actual_completion_date: Optional[datetime] = None
    assigned_by: int
    updated_by: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    group_id: Optional[int] = None
    
    user_name: Optional[str] = None
    training_title: Optional[str] = None
    current_level_name: Optional[str] = None
    assigner_name: Optional[str] = None
    updater_name: Optional[str] = None
    group_name: Optional[str] = None
    exam_questions_count: Optional[int] = None
    exam_duration_minutes: Optional[int] = None

    class Config:
        orm_mode = True

# ---------------- Evaluation Schemas ----------------
class EvaluationBase(BaseModel):
    assignment_id: int
    training_level_id: int
    mcq_score: Optional[float] = None
    practical_score: Optional[float] = None
    assignment_score: Optional[float] = None
    total_score: Optional[float] = None
    max_possible_score: float = 100.0
    status: EvaluationStatus = EvaluationStatus.not_started
    comments: Optional[str] = None

class EvaluationCreate(EvaluationBase):
    attempt_number: int = 1
    evaluation_date: datetime = datetime.utcnow()

class EvaluationUpdate(BaseModel):
    mcq_score: Optional[float] = None
    practical_score: Optional[float] = None
    assignment_score: Optional[float] = None
    total_score: Optional[float] = None
    status: Optional[EvaluationStatus] = None
    comments: Optional[str] = None

class EvaluationOut(EvaluationBase):
    id: int
    assignment_id: int
    training_level_id: int
    attempt_number: int
    evaluation_date: datetime
    percentage_score: float
    is_passing_score: bool
    evaluated_by: int
    created_at: datetime
    
    evaluator_name: Optional[str] = None
    level_name: Optional[str] = None

    class Config:
        orm_mode = True
# Add this to your existing schemas
class LevelDateBase(BaseModel):
    level_id: int
    start_date: Optional[date] = None
    due_date: Optional[date] = None

# Update your AssignmentCreate schema to include level_dates
class AssignmentCreate(BaseModel):
    user_id: int
    training_id: int
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    status: TrainingStatus = TrainingStatus.assigned
    group_id: Optional[int] = None
    level_dates: Optional[Dict[str, Dict[str, date]]] = None  # {level_id: {start_date, due_date}}
# ---------------- Report Schemas ----------------
class OverviewReport(BaseModel):
    # Basic counts
    total_employees: int
    total_trainings: int
    total_assignments: int
    
    # Assignment status breakdown
    assigned_assignments: int
    in_progress_assignments: int
    completed_assignments: int
    failed_assignments: int
    
    # Rates and percentages
    completion_rate: float
    progress_rate: float
    engagement_rate: float
    success_rate: float
    average_score: float
    
    # Additional metrics
    employees_with_assignments: int
    recent_activity: int
    departments_with_assignments: int
    total_categories: int

class UserProgress(BaseModel):
    user_id: int
    user_name: str
    department: str
    total_assignments: int
    completed_assignments: int
    avg_score: float
    current_level: Optional[str] = None

class TrainingProgress(BaseModel):
    training_id: int
    training_title: str
    category: str
    total_assignments: int
    completed_assignments: int
    avg_score: float
    completion_rate: float

# Add to your existing schemas

class TopEmployee(BaseModel):
    employee_id: int
    name: str
    designation: str
    completed: int
    total_assignments: int
    avg_score: float

class TopTraining(BaseModel):
    training_id: int
    title: str
    category: str
    assigned: int
    completed: int
    completion_rate: float
    avg_score: float

class CategoryPerformance(BaseModel):
    category: str
    total_assignments: int
    completed: int
    completion_rate: float
    avg_score: float

class DepartmentPerformance(BaseModel):
    department: str
    total_assignments: int
    completed: int
    completion_rate: float
    avg_score: float

class EnrollmentStats(BaseModel):
    total_enrolled: int
    unique_employees: int
    assigned: int
    in_progress: int
    completed: int
    failed: int

class CompletionMetrics(BaseModel):
    completion_rate: float
    progress_rate: float
    dropout_rate: float

class ScoreStats(BaseModel):
    avg_score: float
    max_score: float
    min_score: float
    score_range: str

class LevelInfo(BaseModel):
    level_id: int
    level_name: str
    level_order: int
    employees_at_level: int
    pass_percentage: float
    duration_hours: Optional[int]

class TrainingStructure(BaseModel):
    total_levels: int
    levels: List[LevelInfo]
    avg_completion_days: Optional[float]

class DepartmentBreakdown(BaseModel):
    department: str
    enrolled: int
    completed: int
    completion_rate: float

class PerformanceIndicators(BaseModel):
    popularity_rank: int
    effectiveness_score: float

class TrainingDetailsReport(BaseModel):
    training_id: int
    title: str
    category: str
    description: Optional[str]
    enrollment_stats: EnrollmentStats
    completion_metrics: CompletionMetrics
    score_stats: ScoreStats
    training_structure: TrainingStructure
    department_breakdown: List[DepartmentBreakdown]
    performance_indicators: PerformanceIndicators
# Add these to your existing schemas.py

class WeeklyProgressBase(BaseModel):
    user_id: int
    assignment_id: int
    training_id: int
    department: str
    week_start_date: datetime
    week_end_date: datetime
    hours_completed: float
    levels_completed: int
    total_score: float
    completion_percentage: float
    current_level: Optional[str] = None
    status: TrainingStatus

class WeeklyProgressCreate(WeeklyProgressBase):
    pass

class WeeklyProgressOut(WeeklyProgressBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    user_name: Optional[str] = None
    training_title: Optional[str] = None

    class Config:
        orm_mode = True

class DepartmentWeeklyProgress(BaseModel):
    department: str
    week_start_date: datetime
    week_end_date: datetime
    total_employees: int
    active_employees: int
    total_hours_completed: float
    avg_completion_percentage: float
    avg_score: float
    levels_completed: int
    employees_completed: int
    employees_in_progress: int
    employees_not_started: int

class EmployeeWeeklyProgress(BaseModel):
    employee_id: int
    employee_name: str
    department: str
    assignment_id: int
    training_title: str
    week_start_date: datetime
    week_end_date: datetime
    hours_completed: float
    levels_completed: int
    total_score: float
    completion_percentage: float
    current_level: str
    status: TrainingStatus

class WeeklyTrainingProgressResponse(BaseModel):
    departments: List[DepartmentWeeklyProgress]
    employees: List[EmployeeWeeklyProgress]
    summary: dict

# Add to your existing schemas.py

class MCQQuestionBase(BaseModel):
    training_level_id: int
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_option: str  # 'A', 'B', 'C', or 'D'
    explanation: Optional[str] = None
    marks: int = 1

class MCQQuestionCreate(MCQQuestionBase):
    # ✅ Added image fields for uploads
    question_image: Optional[str] = None
    option_a_image: Optional[str] = None
    option_b_image: Optional[str] = None
    option_c_image: Optional[str] = None
    option_d_image: Optional[str] = None


    #pass

class MCQQuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    option_a: Optional[str] = None
    option_b: Optional[str] = None
    option_c: Optional[str] = None
    option_d: Optional[str] = None
    correct_option: Optional[str] = None
    explanation: Optional[str] = None
    marks: Optional[int] = None
    is_active: Optional[bool] = None
    # ✅ Added image fields for update
    question_image: Optional[str] = None
    option_a_image: Optional[str] = None
    option_b_image: Optional[str] = None
    option_c_image: Optional[str] = None
    option_d_image: Optional[str] = None

# Add to your existing schemas

class MCQQuestionBulkCreate(BaseModel):
    questions: List[MCQQuestionCreate]

# Add these schemas before your endpoints
class MCQQuestionBulkToggle(BaseModel):
    question_ids: List[int]
    is_active: bool

class MCQQuestionBulkDelete(BaseModel):
    question_ids: List[int]

class CSVUploadResponse(BaseModel):
    message: str
    created_count: int
    error_count: int
    errors: List[str] = []

class ToggleResponse(BaseModel):
    message: str
    question_id: int
    is_active: bool

class BulkToggleResponse(BaseModel):
    message: str
    updated_count: int

class BulkDeleteResponse(BaseModel):
    message: str
    deleted_count: int
    


class MCQQuestionOut(BaseModel):
    id: int
    training_level_id: int
    question_text: str
    question_image: Optional[str] = None
    option_a: str
    option_a_image: Optional[str] = None
    option_b: str
    option_b_image: Optional[str] = None
    option_c: str
    option_c_image: Optional[str] = None
    option_d: str
    option_d_image: Optional[str] = None
    correct_option: str
    explanation: Optional[str] = None
    marks: int
    is_active: bool
    created_by: int
    created_at: datetime
    creator_name: Optional[str] = None


    # ... your other fields ...
    question_image: Optional[str] = None
    option_a_image: Optional[str] = None
    option_b_image: Optional[str] = None
    option_c_image: Optional[str] = None
    option_d_image: Optional[str] = None
    
    @validator('question_image', 'option_a_image', 'option_b_image', 'option_c_image', 'option_d_image', pre=True)
    def ensure_clean_urls(cls, v):
        if v and v.startswith('uploads/'):
            # Ensure the path is properly formatted
            return f"/{v}"  # This makes it "/uploads/mcq_images/filename.jpg"
        return v
    
    class Config:
        orm_mode = True
    
 

class MCQAnswerSubmit(BaseModel):
    question_id: int
    selected_option: str
    time_taken_seconds: int

class MCQExamStart(BaseModel):
    assignment_id: int
    training_level_id: int
    number_of_questions: Optional[int] = Field(None, gt=0, le=100)  # Allow 1-100 questions, optional
    duration_minutes: Optional[int] = Field(None, gt=0, le=180)    # Allow 1-180 minutes, optional
    
    @validator('number_of_questions')
    def validate_questions_count(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Number of questions must be positive')
        return v
    
    @validator('duration_minutes')
    def validate_duration(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Duration must be positive')
        return v

class MCQExamSubmit(BaseModel):
    answers: List['MCQAnswerSubmit']
    total_time_taken_seconds: int

class BulkExamSettingsUpdate(BaseModel):
    level_id: int
    exam_questions_count: Optional[int] = Field(None, gt=0, le=100)
    exam_duration_minutes: Optional[int] = Field(None, gt=0, le=180)

class ExamSettingsUpdate(BaseModel):
    exam_questions_count: Optional[int] = Field(None, gt=0, le=100)
    exam_duration_minutes: Optional[int] = Field(None, gt=0, le=180)

class MCQExamAttemptOut(BaseModel):
    id: int
    assignment_id: int
    training_level_id: int
    attempt_number: int
    total_questions: int
    questions_attempted: int
    correct_answers: int
    total_marks: float
    percentage_score: float
    time_taken_seconds: int
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    training_level_name: Optional[str] = None
    passing_percentage: Optional[float] = None

    class Config:
        orm_mode = True

class MCQExamResultDetail(BaseModel):
    question_id: int
    question_text: str
    options: dict  # {'A': 'option A', 'B': 'option B', ...}
    correct_option: str
    selected_option: Optional[str] = None
    is_correct: bool
    explanation: Optional[str] = None
    marks_obtained: float

class MCQExamDetailedResult(MCQExamAttemptOut):
    details: List[MCQExamResultDetail]


class UserUpdateEmployee(BaseModel):
    user_code: str | None = None
    designation: str | None = None
    department: str | None = None
    position: str | None = None
    password: str | None = None
#+++++++
from enum import Enum
from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime

# Add these enum classes
class ReportType(str, Enum):
    ISSUE = "issue"
    FEEDBACK = "feedback"

class ReportStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"

class ReportPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

# Add these schemas
class ReportCreate(BaseModel):
    report_type: ReportType
    title: str
    description: str
    priority: ReportPriority = ReportPriority.MEDIUM
    
    @validator('title')
    def title_length(cls, v):
        if len(v) < 5:
            raise ValueError('Title must be at least 5 characters long')
        if len(v) > 255:
            raise ValueError('Title cannot exceed 255 characters')
        return v
    
    @validator('description')
    def description_length(cls, v):
        if len(v) < 10:
            raise ValueError('Description must be at least 10 characters long')
        return v

class CommentCreate(BaseModel):
    comment_text: str
    
    @validator('comment_text')
    def comment_length(cls, v):
        if len(v) < 1:
            raise ValueError('Comment cannot be empty')
        if len(v) > 1000:
            raise ValueError('Comment cannot exceed 1000 characters')
        return v

class ReportResponse(BaseModel):
    id: int
    user_id: int
    user_name: str
    report_type: ReportType
    title: str
    description: str
    status: ReportStatus
    priority: ReportPriority
    attachment_url: Optional[str]
    attachment_type: Optional[str]
    original_filename: Optional[str]
    created_at: datetime
    updated_at: datetime
    comment_count: int = 0
    
    class Config:
        orm_mode = True

class CommentResponse(BaseModel):
    id: int
    report_id: int
    user_id: int
    user_name: str
    comment_text: str
    attachment_url: Optional[str]
    created_at: datetime
    
    class Config:
        orm_mode = True

class ReportDetailResponse(ReportResponse):
    comments: List[CommentResponse] = []


#+++++++++++
# Add these to your existing schemas.py

class EmployeeGroupBase(BaseModel):
    name: str
    description: Optional[str] = None
    project_name: str

class EmployeeGroupCreate(EmployeeGroupBase):
    employee_ids: List[int] = []

class EmployeeGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_name: Optional[str] = None
    employee_ids: Optional[List[int]] = None
    class Config:
            from_attributes = True

class EmployeeGroupOut(EmployeeGroupBase):
    id: int
    group_id: int # Added explicit group_id field
    created_by: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    member_count: int
    creator_name: str
    members: List[dict] = []

    class Config:
        orm_mode = True

class GroupTrainingAssign(BaseModel):
    group_id: int
    training_id: int
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    level_dates: Optional[Dict[str, Dict[str, date]]] = None

# class TrainingDurationSettings(BaseModel):
#     level_durations: List[dict]

class BulkAssignmentsOperations(BaseModel):
    assignment_ids: List[int]
    operation: str  # "delete", "reset", "update_status"
    new_status: Optional[str] = None
        

class CertificateResponse(BaseModel):
    employee_name: str
    employee_code: str
    training_title: str
    training_category: str
    completion_date: datetime
    certificate_id: str
    issued_at: Optional[datetime] = None
    congratulatory_message: str = "Congratulations on successfully completing this training program!"
    
    class Config:
        orm_mode = True

class NotificationBase(BaseModel):
    user_id: int
    title: str
    message: str
    is_read: bool = False
    created_at: Optional[datetime] = None

class NotificationCreate(BaseModel):
    user_id: int
    title: str
    message: str

class NotificationOut(NotificationBase):
    id: int
    class Config:
        orm_mode = True

