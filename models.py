from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Float, 
    Enum, Text, Boolean, func, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from database import Base
import enum
from datetime import datetime, date, time

# ...existing code...

# ---------------- Notification Model ----------------
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User")
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Float, 
    Enum, Text, Boolean, func, UniqueConstraint, JSON
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from database import Base
import enum
from datetime import datetime, date,time

# ---------------- Enums ----------------
class UserRole(str, enum.Enum):
    admin = "admin"
    employee = "employee"

class TrainingStatus(str, enum.Enum):
    assigned = "assigned"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"

class TrainingLevelEnum(str, enum.Enum):
    basic = "basic"
    intermediate = "intermediate"
    advanced = "advanced"

class EvaluationStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    passed = "passed"
    failed = "failed"

# ---------------- Users ----------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_code = Column(String, unique=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    designation = Column(String, nullable=True)
    department = Column(String, nullable=False)
    position = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole, name="userrole"), default=UserRole.employee, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    reports = relationship("UserReport", back_populates="user")
    created_trainings = relationship("Training", foreign_keys="Training.created_by", back_populates="creator")
    updated_trainings = relationship("Training", foreign_keys="Training.updated_by", back_populates="updater")
    assignments = relationship("Assignment", foreign_keys="Assignment.user_id", back_populates="user")
    created_assignments = relationship("Assignment", foreign_keys="Assignment.assigned_by", back_populates="assigner")
    updated_assignments = relationship("Assignment", foreign_keys="Assignment.updated_by", back_populates="updater")
    training_levels = relationship("TrainingLevel", back_populates="creator")
    evaluations = relationship("Evaluation", back_populates="evaluator")
    certificates = relationship("Certificate", back_populates="user")

    @validates("user_code")
    def validate_user_code(self, key, value):
        if not value.startswith("AASPL-"):
            return f"AASPL-{value}"
        return value

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

# ---------------- Trainings ----------------
class Training(Base):
    __tablename__ = "trainings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, unique=True)
    description = Column(Text)
    category = Column(String, nullable=False)
    has_levels = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_trainings")
    updater = relationship("User", foreign_keys=[updated_by], back_populates="updated_trainings")
    levels = relationship("TrainingLevel", back_populates="training", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="training", cascade="all, delete-orphan")
 
# ---------------- Training Levels ----------------
class TrainingLevel(Base):
    __tablename__ = "training_levels"

    id = Column(Integer, primary_key=True, index=True)
    training_id = Column(Integer, ForeignKey("trainings.id"), nullable=False)
    level_name = Column(String, nullable=False)
    level_order = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    duration_hours = Column(Integer, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    prerequisites = Column(Text, nullable=True)
    learning_objectives = Column(Text, nullable=True)
    learning_plan_links = Column(Text, nullable=True)
    learning_materials = Column(Text, nullable=True)
    pass_percentage = Column(Float, default=60.0)
    max_attempts = Column(Integer, default=3)
    exam_questions_count = Column(Integer, default=50)  # Number of questions in exam
    exam_duration_minutes = Column(Integer, default=20)  # Exam duration in minutes

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    training = relationship("Training", back_populates="levels")
    creator = relationship("User", back_populates="training_levels")
    assignments = relationship("Assignment", back_populates="current_level")
    evaluations = relationship("Evaluation", back_populates="training_level")
    mcq_questions = relationship("MCQQuestion", back_populates="training_level", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("training_id", "level_order", name="uq_training_level_order"),
        UniqueConstraint("training_id", "level_name", name="uq_training_level_name"),
    )

# ---------------- Assignments ----------------
class Assignment(Base):
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    training_id = Column(Integer, ForeignKey("trainings.id"), nullable=False)
    current_level_id = Column(Integer, ForeignKey("training_levels.id"), nullable=False)
    
    status = Column(Enum(TrainingStatus, name="trainingstatus"), default=TrainingStatus.assigned)
    training_start_date = Column(DateTime, nullable=True)
    training_end_date = Column(DateTime, nullable=True)
    actual_completion_date = Column(DateTime, nullable=True)
    
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("employee_groups.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
#++++++

#+++++++
    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="assignments")
    training = relationship("Training", back_populates="assignments")
    current_level = relationship("TrainingLevel", foreign_keys=[current_level_id], back_populates="assignments")
    assigner = relationship("User", foreign_keys=[assigned_by], back_populates="created_assignments")
    updater = relationship("User", foreign_keys=[updated_by], back_populates="updated_assignments")
    evaluations = relationship("Evaluation", back_populates="assignment", cascade="all, delete-orphan")
    mcq_exam_attempts = relationship("MCQExamAttempt", back_populates="assignment", cascade="all, delete-orphan")
    training_progress = relationship("TrainingProgress", back_populates="assignment", cascade="all, delete-orphan")
    level_dates = relationship("AssignmentLevelDate", back_populates="assignment", cascade="all, delete-orphan")
    certificate = relationship("Certificate", back_populates="assignment", uselist=False)
    group = relationship("EmployeeGroup")

    def progress_to_next_level(self, db):
        from sqlalchemy import and_
        current_level_order = self.current_level.level_order
        next_level = db.query(TrainingLevel).filter(
            and_(
                TrainingLevel.training_id == self.training_id,
                TrainingLevel.level_order == current_level_order + 1
            )
        ).first()
        
        if next_level:
            self.current_level_id = next_level.id
            self.status = TrainingStatus.assigned
            return True
        return False

# ---------------- Evaluations ----------------
class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    training_level_id = Column(Integer, ForeignKey("training_levels.id"), nullable=False)
    
    attempt_number = Column(Integer, default=1)
    evaluation_date = Column(DateTime, nullable=False, server_default=func.now())
    mcq_score = Column(Float, nullable=True)
    practical_score = Column(Float, nullable=True)
    assignment_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    max_possible_score = Column(Float, default=100.0)
    status = Column(Enum(EvaluationStatus, name="evaluationstatus"), default=EvaluationStatus.not_started)
    comments = Column(Text, nullable=True)
    
    evaluated_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    assignment = relationship("Assignment", back_populates="evaluations")
    training_level = relationship("TrainingLevel", back_populates="evaluations")
    evaluator = relationship("User", back_populates="evaluations")

    @hybrid_property
    def percentage_score(self):
        if self.total_score is None or self.max_possible_score == 0:
            return 0.0
        return (self.total_score / self.max_possible_score) * 100

    @hybrid_property
    def is_passing_score(self):
        if not self.training_level or self.total_score is None:
            return False
        return self.percentage_score >= self.training_level.pass_percentage
    
# ---------------- Training Progress ----------------
class TrainingProgress(Base):
    __tablename__ = "training_progress"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    training_id = Column(Integer, ForeignKey("trainings.id"), nullable=False)
    department = Column(String, nullable=False)
    week_start_date = Column(DateTime, nullable=False)
    week_end_date = Column(DateTime, nullable=False)
    
    # Progress metrics
    hours_completed = Column(Float, default=0.0)
    levels_completed = Column(Integer, default=0)
    total_score = Column(Float, default=0.0)
    completion_percentage = Column(Float, default=0.0)
    
    # Status tracking
    current_level = Column(String, nullable=True)
    status = Column(Enum(TrainingStatus), default=TrainingStatus.assigned)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    assignment = relationship("Assignment", foreign_keys=[assignment_id])
    training = relationship("Training", foreign_keys=[training_id])

    __table_args__ = (
        UniqueConstraint('user_id', 'assignment_id', 'week_start_date', 
                        name='uq_user_assignment_week'),
    )

# ---------------- MCQ Questions ----------------
class MCQQuestion(Base):
    __tablename__ = "mcq_questions"

    id = Column(Integer, primary_key=True, index=True)
    training_level_id = Column(Integer, ForeignKey("training_levels.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    option_c = Column(String, nullable=False)
    option_d = Column(String, nullable=False)
    question_image = Column(String, nullable=True)
    option_a_image = Column(String, nullable=True)
    option_b_image = Column(String, nullable=True)
    option_c_image = Column(String, nullable=True)
    option_d_image = Column(String, nullable=True)

    correct_option = Column(Enum('A', 'B', 'C', 'D', name="mcq_option"), nullable=False)
    explanation = Column(Text, nullable=True)
    marks = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    training_level = relationship("TrainingLevel", back_populates="mcq_questions")
    creator = relationship("User", foreign_keys=[created_by])

# ---------------- MCQ Exam Attempts ----------------
class MCQExamAttempt(Base):
    __tablename__ = "mcq_exam_attempts"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    training_level_id = Column(Integer, ForeignKey("training_levels.id"), nullable=False)
    attempt_number = Column(Integer, default=1)
    total_questions = Column(Integer, nullable=False)
    questions_attempted = Column(Integer, default=0)
    correct_answers = Column(Integer, default=0)
    total_marks = Column(Float, default=0.0)
    percentage_score = Column(Float, default=0.0)
    time_taken_seconds = Column(Integer, default=0)
    status = Column(Enum('in_progress', 'completed', 'passed', 'failed', name="exam_status"), default='in_progress')
    duration_minutes = Column(Integer, default=25)

    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    #+++++++++
    # Add overlaps parameter to resolve the warning
    proctoring = relationship("ExamProctoring", back_populates="exam_attempt", 
                            uselist=False, overlaps="proctoring_data")
    
    # If you have another relationship named proctoring_data, make sure they don't overlap
    proctoring_data = relationship("ExamProctoring", viewonly=True, 
                                 overlaps="proctoring")


    #++++++++++
    
    # Relationships
    assignment = relationship("Assignment", back_populates="mcq_exam_attempts")
    training_level = relationship("TrainingLevel")
    answers = relationship("MCQAnswer", back_populates="exam_attempt", cascade="all, delete-orphan")
    proctoring_data = relationship("ExamProctoring", back_populates="exam_attempt", cascade="all, delete-orphan")
    session_data = relationship("ExamSessionData", back_populates="exam_attempt", cascade="all, delete-orphan")
    proctoring = relationship("ExamProctoring", back_populates="exam_attempt", uselist=False)

# ---------------- MCQ Answers ----------------
class MCQAnswer(Base):
    __tablename__ = "mcq_answers"

    id = Column(Integer, primary_key=True, index=True)
    exam_attempt_id = Column(Integer, ForeignKey("mcq_exam_attempts.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("mcq_questions.id"), nullable=False)
    selected_option = Column(Enum('A', 'B', 'C', 'D', name="mcq_option"), nullable=True)
    is_correct = Column(Boolean, default=False)
    marks_obtained = Column(Float, default=0.0)
    time_taken_seconds = Column(Integer, default=0)
    
    answered_at = Column(DateTime, server_default=func.now())

    # Relationships
    exam_attempt = relationship("MCQExamAttempt", back_populates="answers")
    question = relationship("MCQQuestion")

# ---------------- Assignment Level Dates ----------------
class AssignmentLevelDate(Base):
    __tablename__ = "assignment_level_dates"
    
    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=False)
    level_id = Column(Integer, ForeignKey("training_levels.id"), nullable=False)
    start_date = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    assignment = relationship("Assignment", back_populates="level_dates")
    level = relationship("TrainingLevel")

# ---------------- User Reports ----------------
class UserReport(Base):
    __tablename__ = "user_reports"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    report_type = Column(String(20), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(20), default='pending')
    priority = Column(String(20), default='medium')
    
    # File attachments
    attachment_url = Column(String(500), nullable=True)
    attachment_type = Column(String(20), nullable=True)
    original_filename = Column(String(255), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="reports")
    comments = relationship("ReportComment", back_populates="report", cascade="all, delete-orphan")

class ReportComment(Base):
    __tablename__ = "report_comments"
    
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("user_reports.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    comment_text = Column(Text, nullable=False)
    attachment_url = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    report = relationship("UserReport", back_populates="comments")
    user = relationship("User")

# ---------------- Exam Settings ----------------
class ExamSettings(Base):
    __tablename__ = "exam_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    allowed_browsers = Column(JSON, default=["chrome", "edge"])
    max_windows_allowed = Column(Integer, default=1)
    max_popup_warnings = Column(Integer, default=3)
    face_detection_required = Column(Boolean, default=True)
    screen_capture_enabled = Column(Boolean, default=True)
    copy_paste_blocked = Column(Boolean, default=True)
    developer_tools_blocked = Column(Boolean, default=True)

# ---------------- Exam Proctoring ----------------
class ExamProctoring(Base):
    __tablename__ = "exam_proctoring"
    
    id = Column(Integer, primary_key=True, index=True)
    exam_attempt_id = Column(Integer, ForeignKey("mcq_exam_attempts.id"))  # Fixed: plural 'attempts'
    face_detected = Column(Boolean, default=True)
    movement_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    screenshots_taken = Column(JSON)
    violations_detected = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    exam_attempt = relationship("MCQExamAttempt", back_populates="proctoring")

# ---------------- Exam Session Data ----------------
class ExamSessionData(Base):
    __tablename__ = "exam_session_data"
    
    id = Column(Integer, primary_key=True, index=True)
    exam_attempt_id = Column(Integer, ForeignKey("mcq_exam_attempts.id"))  # Fixed: plural 'attempts'
    session_data = Column(JSON)
    screenshots = Column(JSON)
    violations = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)

    # Relationship
    exam_attempt = relationship("MCQExamAttempt", back_populates="session_data")


  #+++++++++
  # Add these to your existing models.py

class EmployeeGroup(Base):
    __tablename__ = "employee_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text, nullable=True)
    project_name = Column(String, index=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    creator = relationship("User", backref="created_groups")
    members = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")


# models.py
class GroupMember(Base):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("employee_groups.id"))
    employee_id = Column(Integer, ForeignKey("users.id"))
    added_by = Column(Integer, ForeignKey("users.id"))
    added_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    group = relationship("EmployeeGroup", back_populates="members")
    employee = relationship("User", foreign_keys=[employee_id])
    added_by_user = relationship("User", foreign_keys=[added_by])

# ---------------- Certificates ----------------
class Certificate(Base):
    __tablename__ = "certificates"

    id = Column(Integer, primary_key=True, index=True)
    certificate_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    training_id = Column(Integer, ForeignKey("trainings.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("assignments.id"), unique=True, nullable=False)
    completion_date = Column(DateTime, nullable=False)
    issued_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="certificates")
    training = relationship("Training")
    assignment = relationship("Assignment", back_populates="certificate")
