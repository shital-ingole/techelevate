from passlib.context import CryptContext
import re

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def generate_user_code(existing_codes: list, base_code: str = "AASPL") -> str:
    """Generate unique user code"""
    if not existing_codes:
        return f"{base_code}-0001"
    
    # Extract numbers from existing codes
    numbers = []
    for code in existing_codes:
        if code and code.startswith(base_code):
            try:
                num = int(code.split('-')[-1])
                numbers.append(num)
            except ValueError:
                continue
    
    if numbers:
        next_num = max(numbers) + 1
    else:
        next_num = 1
    
    return f"{base_code}-{next_num:04d}"

def validate_email_format(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def calculate_overall_score(scores_dict):
    """
    Calculate overall score from individual component scores
    """
    mcq_weight = 0.4
    practical_weight = 0.4
    assignment_weight = 0.2
    
    mcq_score = scores_dict.get('mcq_score', 0) or 0
    practical_score = scores_dict.get('practical_score', 0) or 0
    assignment_score = scores_dict.get('assignment_score', 0) or 0
    
    # If only total score is provided, use that
    if scores_dict.get('total_score') is not None:
        return scores_dict['total_score']
    
    # Calculate weighted average
    weighted_score = (
        (mcq_score * mcq_weight) +
        (practical_score * practical_weight) +
        (assignment_score * assignment_weight)
    )
    
    return round(weighted_score, 2)