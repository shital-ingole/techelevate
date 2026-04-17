import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Template
import os
from datetime import datetime

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender_email = os.getenv("SMTP_USERNAME")
        self.sender_password = os.getenv("SMTP_PASSWORD")
        self.use_tls = os.getenv("SMTP_USE_TLS", "True").lower() == "true"
        self.company_name = "Aligned Automation Solutions Private Limited"
        self.dashboard_url = os.getenv("DASHBOARD_URL", "https://your-dashboard-domain.com")

    def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        """
        Send email using SMTP
        """
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = f"HR Department <{self.sender_email}>"
            msg['To'] = to_email
            msg['Subject'] = subject

            # Add HTML content
            msg.attach(MIMEText(html_content, 'html'))

            # Create SMTP session
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
            
            print(f"Email sent successfully to {to_email}")
            return True

        except Exception as e:
            print(f"Failed to send email to {to_email}: {str(e)}")
            return False

    def send_training_assignment_email(
        self, 
        employee_email: str, 
        employee_name: str,
        training_title: str,
        training_description: str,
        category: str,
        current_level: str,
        level_description: str,
        duration_hours: int,
        prerequisites: str,
        learning_objectives: str,
        training_start_date: str,
        training_end_date: str,
        learning_plan_links: str = "",
        learning_materials: str = ""
    ) -> bool:
        """
        Send training assignment email to employee with all details
        """
        subject = f"Training Program Assignment: {training_title} - Level: {current_level}"

        html_content = self._render_training_assignment_template(
            employee_name=employee_name,
            training_title=training_title,
            training_description=training_description,
            category=category,
            current_level=current_level,
            level_description=level_description,
            duration_hours=duration_hours,
            prerequisites=prerequisites,
            learning_objectives=learning_objectives,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            learning_plan_links=learning_plan_links,
            learning_materials=learning_materials
        )

        return self.send_email(employee_email, subject, html_content)

    def send_level_progression_email(
        self,
        employee_email: str,
        employee_name: str,
        training_title: str,
        previous_level: str,
        new_level: str,
        new_level_description: str,
        duration_hours: int,
        prerequisites: str,
        learning_objectives: str,
        learning_plan_links: str = "",
        learning_materials: str = ""
    ) -> bool:
        """
        Send email when employee progresses to next level
        """
        subject = f"Training Progress Update: {training_title} - Advanced to {new_level} Level"

        html_content = self._render_level_progression_template(
            employee_name=employee_name,
            training_title=training_title,
            previous_level=previous_level,
            new_level=new_level,
            new_level_description=new_level_description,
            duration_hours=duration_hours,
            prerequisites=prerequisites,
            learning_objectives=learning_objectives,
            learning_plan_links=learning_plan_links,
            learning_materials=learning_materials
        )

        return self.send_email(employee_email, subject, html_content)

    def _render_training_assignment_template(self, **context) -> str:
        """
        Render formal training assignment email template with all details
        """
        template_str = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #000000;
                    margin: 0;
                    padding: 20px;
                    background-color: #ffffff;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    background: #ffffff;
                    border: 1px solid #cccccc;
                }
                .header {
                    background: #1a365d;
                    color: #ffffff;
                    padding: 20px;
                    text-align: center;
                    border-bottom: 2px solid #000000;
                }
                .header h1 {
                    margin: 0;
                    font-size: 20px;
                    font-weight: bold;
                }
                .company-name {
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 5px;
                }
                .content {
                    padding: 25px;
                }
                .section {
                    margin-bottom: 20px;
                    padding-bottom: 15px;
                    border-bottom: 1px solid #dddddd;
                }
                .section-title {
                    color: #1a365d;
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 10px;
                    text-decoration: underline;
                }
                .info-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin: 10px 0;
                    border: 1px solid #dddddd;
                }
                .info-table th {
                    background: #f8f9fa;
                    border: 1px solid #dddddd;
                    padding: 8px;
                    text-align: left;
                    font-weight: bold;
                    width: 30%;
                }
                .info-table td {
                    border: 1px solid #dddddd;
                    padding: 8px;
                    vertical-align: top;
                }
                .level-indicator {
                    background: #1a365d;
                    color: white;
                    padding: 5px 10px;
                    font-size: 14px;
                    font-weight: bold;
                    display: inline-block;
                    margin-bottom: 10px;
                }
                .instructions {
                    background: #f8f9fa;
                    padding: 15px;
                    border: 1px solid #dddddd;
                    margin: 15px 0;
                }
                .instruction-step {
                    margin-bottom: 8px;
                    padding-left: 15px;
                }
                .links-section {
                    margin: 15px 0;
                }
                .link-item {
                    margin-bottom: 5px;
                }
                .dashboard-button {
                    display: inline-block;
                    background: #1a365d;
                    color: white;
                    padding: 12px 24px;
                    text-decoration: none;
                    border-radius: 4px;
                    font-weight: bold;
                    margin: 10px 0;
                    text-align: center;
                }
                .dashboard-button:hover {
                    background: #2d4a7c;
                }
                .footer {
                    background: #f8f9fa;
                    padding: 20px;
                    border-top: 1px solid #dddddd;
                    font-size: 12px;
                    color: #666666;
                }
                .contact-info {
                    margin-top: 15px;
                    padding-top: 15px;
                    border-top: 1px solid #dddddd;
                }
                .regards {
                    margin-top: 20px;
                    margin-bottom: 10px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <!-- Header -->
                <div class="header">
                    <div class="company-name">ALIGNED AUTOMATION SOLUTIONS PRIVATE LIMITED</div>
                    <h1>TRAINING PROGRAM ASSIGNMENT NOTIFICATION</h1>
                </div>
                
                <!-- Content -->
                <div class="content">
                    <div class="section">
                        <p><strong>Date:</strong> {{ current_date }}</p>
                        <p><strong>To:</strong> {{ employee_name }}</p>
                        <p><strong>From:</strong> Human Resources Department</p>
                    </div>

                    <div class="section">
                        <p>Dear {{ employee_name }},</p>
                        <p>This is to formally notify you that you have been assigned to the following training program as part of your professional development plan at Aligned Automation Solutions Private Limited.</p>
                    </div>

                    <!-- Quick Access -->
                    <div class="section">
                        <div class="section-title">QUICK ACCESS</div>
                        <p>You can access your training dashboard directly by clicking the button below:</p>
                        <a href="{{ dashboard_url }}" class="dashboard-button" target="_blank">
                            ACCESS TRAINING DASHBOARD
                        </a>
                        <p><small>Alternatively, you can copy and paste this link in your browser:<br>
                        <code>{{ dashboard_url }}</code></small></p>
                    </div>

                    <!-- Program Overview -->
                    <div class="section">
                        <div class="section-title">TRAINING PROGRAM OVERVIEW</div>
                        <table class="info-table">
                            <tr>
                                <th>Training Program Title:</th>
                                <td>{{ training_title }}</td>
                            </tr>
                            <tr>
                                <th>Program Category:</th>
                                <td>{{ category }}</td>
                            </tr>
                            <tr>
                                <th>Training Period:</th>
                                <td>{{ training_start_date }} to {{ training_end_date }}</td>
                            </tr>
                            {% if training_description %}
                            <tr>
                                <th>Program Description:</th>
                                <td>{{ training_description }}</td>
                            </tr>
                            {% endif %}
                        </table>
                    </div>

                    <!-- Current Level Details -->
                    <div class="section">
                        <div class="section-title">ASSIGNED LEVEL DETAILS</div>
                        <div class="level-indicator">CURRENT LEVEL: {{ current_level|upper }}</div>
                        <table class="info-table">
                            <tr>
                                <th>Level Description:</th>
                                <td>{{ level_description }}</td>
                            </tr>
                            <tr>
                                <th>Estimated Duration:</th>
                                <td>{{ duration_hours }} Hours</td>
                            </tr>
                            <tr>
                                <th>Passing Requirement:</th>
                                <td>Minimum 60% Score</td>
                            </tr>
                            {% if prerequisites %}
                            <tr>
                                <th>Prerequisites:</th>
                                <td>{{ prerequisites }}</td>
                            </tr>
                            {% endif %}
                        </table>
                        
                        {% if learning_objectives %}
                        <div style="margin-top: 15px;">
                            <div style="font-weight: bold; margin-bottom: 5px;">Learning Objectives:</div>
                            <div style="white-space: pre-line;">{{ learning_objectives }}</div>
                        </div>
                        {% endif %}
                    </div>

                    <!-- Learning Resources -->
                    {% if learning_plan_links or learning_materials %}
                    <div class="section">
                        <div class="section-title">LEARNING RESOURCES</div>
                        <div class="links-section">
                            {% if learning_plan_links %}
                            <div class="link-item">
                                <strong>Learning Plan Links:</strong><br>
                                <div style="white-space: pre-line;">{{ learning_plan_links }}</div>
                            </div>
                            {% endif %}
                            
                            {% if learning_materials %}
                            <div class="link-item">
                                <strong>Learning Materials:</strong><br>
                                <div style="white-space: pre-line;">{{ learning_materials }}</div>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                    {% endif %}

                    <!-- Action Required -->
                    <div class="section">
                        <div class="section-title">REQUIRED ACTIONS</div>
                        <div class="instructions">
                            <p><strong>Please complete the following steps:</strong></p>
                            <div class="instruction-step">1. <strong>Click the "ACCESS TRAINING DASHBOARD" button above</strong> to access your training portal</div>
                            <div class="instruction-step">2. Review all learning materials for the {{ current_level }} level</div>
                            <div class="instruction-step">3. Complete all assigned modules and assessments</div>
                            <div class="instruction-step">4. Submit required assignments within the specified timeframe</div>
                            <div class="instruction-step">5. Achieve minimum passing score of 60% to progress to next level</div>
                            <div class="instruction-step">6. Contact your reporting manager for technical assistance</div>
                        </div>
                    </div>

                    <!-- Important Notes -->
                    <div class="section">
                        <div class="section-title">IMPORTANT NOTES</div>
                        <ul>
                            <li>This training is mandatory and must be completed by the specified end date</li>
                            <li>Your progress will be monitored and evaluated regularly</li>
                            <li>Successful completion is required for career progression</li>
                            <li>Maintain regular communication with your training coordinator</li>
                            <li>Use the dashboard to track your progress and access all training materials</li>
                        </ul>
                    </div>

                    <div class="regards">
                        <p>Yours sincerely,</p>
                        <p><strong>Human Resources Department</strong><br>
                        Aligned Automation Solutions Private Limited</p>
                    </div>
                </div>

                <!-- Footer -->
                <div class="footer">
                    <p><strong>CONFIDENTIALITY NOTICE:</strong> This email and any attachments are confidential and intended solely for the use of the individual to whom they are addressed. If you are not the intended recipient, you are strictly prohibited from disclosing, distributing, copying or in any way using this information. Please notify the sender immediately and delete this email from your system.</p>
                    
                    <div class="contact-info">
                        <p><strong>Aligned Automation Solutions Private Limited</strong><br>
                        Email: hr@alignedautomation.com | Phone: +91-XX-XXXX-XXXX<br>
                        Registered Office: [Company Address]</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        template = Template(template_str)
        current_date = datetime.now().strftime("%B %d, %Y")
        return template.render(current_date=current_date, dashboard_url=self.dashboard_url, **context)

    def _render_level_progression_template(self, **context) -> str:
        """
        Render formal level progression email template with all details
        """
        template_str = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #000000;
                    margin: 0;
                    padding: 20px;
                    background-color: #ffffff;
                }
                .container {
                    max-width: 800px;
                    margin: 0 auto;
                    background: #ffffff;
                    border: 1px solid #cccccc;
                }
                .header {
                    background: #1a365d;
                    color: #ffffff;
                    padding: 20px;
                    text-align: center;
                    border-bottom: 2px solid #000000;
                }
                .header h1 {
                    margin: 0;
                    font-size: 20px;
                    font-weight: bold;
                }
                .company-name {
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 5px;
                }
                .content {
                    padding: 25px;
                }
                .section {
                    margin-bottom: 20px;
                    padding-bottom: 15px;
                    border-bottom: 1px solid #dddddd;
                }
                .section-title {
                    color: #1a365d;
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 10px;
                    text-decoration: underline;
                }
                .info-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin: 10px 0;
                    border: 1px solid #dddddd;
                }
                .info-table th {
                    background: #f8f9fa;
                    border: 1px solid #dddddd;
                    padding: 8px;
                    text-align: left;
                    font-weight: bold;
                    width: 30%;
                }
                .info-table td {
                    border: 1px solid #dddddd;
                    padding: 8px;
                    vertical-align: top;
                }
                .progress-notice {
                    background: #f0f8ff;
                    border: 1px solid #1a365d;
                    padding: 15px;
                    margin: 15px 0;
                }
                .level-progression {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 20px 0;
                    padding: 15px;
                    background: #f8f9fa;
                    border: 1px solid #dddddd;
                }
                .level-box {
                    padding: 10px 20px;
                    border: 2px solid #1a365d;
                    font-weight: bold;
                    text-align: center;
                    margin: 0 10px;
                }
                .level-completed {
                    background: #1a365d;
                    color: white;
                }
                .level-current {
                    background: white;
                    color: #1a365d;
                }
                .arrow {
                    font-size: 18px;
                    color: #1a365d;
                    font-weight: bold;
                }
                .instructions {
                    background: #f8f9fa;
                    padding: 15px;
                    border: 1px solid #dddddd;
                    margin: 15px 0;
                }
                .instruction-step {
                    margin-bottom: 8px;
                    padding-left: 15px;
                }
                .links-section {
                    margin: 15px 0;
                }
                .link-item {
                    margin-bottom: 5px;
                }
                .dashboard-button {
                    display: inline-block;
                    background: #1a365d;
                    color: white;
                    padding: 12px 24px;
                    text-decoration: none;
                    border-radius: 4px;
                    font-weight: bold;
                    margin: 10px 0;
                    text-align: center;
                }
                .dashboard-button:hover {
                    background: #2d4a7c;
                }
                .footer {
                    background: #f8f9fa;
                    padding: 20px;
                    border-top: 1px solid #dddddd;
                    font-size: 12px;
                    color: #666666;
                }
                .contact-info {
                    margin-top: 15px;
                    padding-top: 15px;
                    border-top: 1px solid #dddddd;
                }
                .regards {
                    margin-top: 20px;
                    margin-bottom: 10px;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <!-- Header -->
                <div class="header">
                    <div class="company-name">ALIGNED AUTOMATION SOLUTIONS PRIVATE LIMITED</div>
                    <h1>TRAINING PROGRESS UPDATE NOTIFICATION</h1>
                </div>
                
                <!-- Content -->
                <div class="content">
                    <div class="section">
                        <p><strong>Date:</strong> {{ current_date }}</p>
                        <p><strong>To:</strong> {{ employee_name }}</p>
                        <p><strong>From:</strong> Human Resources Department</p>
                    </div>

                    <div class="section">
                        <p>Dear {{ employee_name }},</p>
                        
                        <div class="progress-notice">
                            <p><strong>PROGRESS ACHIEVEMENT:</strong> We are pleased to inform you that you have successfully completed the <strong>{{ previous_level }}</strong> level and have been advanced to the <strong>{{ new_level }}</strong> level in your training program.</p>
                        </div>
                    </div>

                    <!-- Quick Access -->
                    <div class="section">
                        <div class="section-title">QUICK ACCESS</div>
                        <p>Access your training dashboard to continue with the next level:</p>
                        <a href="{{ dashboard_url }}" class="dashboard-button" target="_blank">
                            CONTINUE TO TRAINING DASHBOARD
                        </a>
                        <p><small>Alternatively, you can copy and paste this link in your browser:<br>
                        <code>{{ dashboard_url }}</code></small></p>
                    </div>

                    <!-- Program Information -->
                    <div class="section">
                        <div class="section-title">TRAINING PROGRAM INFORMATION</div>
                        <table class="info-table">
                            <tr>
                                <th>Training Program:</th>
                                <td>{{ training_title }}</td>
                            </tr>
                            <tr>
                                <th>Current Level:</th>
                                <td>{{ new_level }}</td>
                            </tr>
                            <tr>
                                <th>Progress Status:</th>
                                <td>Successfully completed {{ previous_level }} level</td>
                            </tr>
                        </table>
                    </div>

                    <!-- Level Progression -->
                    <div class="section">
                        <div class="section-title">LEVEL PROGRESSION STATUS</div>
                        <div class="level-progression">
                            <div class="level-box level-completed">{{ previous_level|upper }}</div>
                            <div class="arrow">→</div>
                            <div class="level-box level-current">{{ new_level|upper }}</div>
                        </div>
                        <p style="text-align: center; font-weight: bold; margin-top: 10px;">Level Progression: {{ previous_level }} → {{ new_level }}</p>
                    </div>

                    <!-- Next Level Requirements -->
                    <div class="section">
                        <div class="section-title">NEXT LEVEL REQUIREMENTS</div>
                        <table class="info-table">
                            <tr>
                                <th>Level Description:</th>
                                <td>{{ new_level_description }}</td>
                            </tr>
                            <tr>
                                <th>Estimated Duration:</th>
                                <td>{{ duration_hours }} Hours</td>
                            </tr>
                            <tr>
                                <th>Passing Requirement:</th>
                                <td>Minimum 60% Score</td>
                            </tr>
                            {% if prerequisites %}
                            <tr>
                                <th>Prerequisites:</th>
                                <td>{{ prerequisites }}</td>
                            </tr>
                            {% endif %}
                        </table>
                        
                        {% if learning_objectives %}
                        <div style="margin-top: 15px;">
                            <div style="font-weight: bold; margin-bottom: 5px;">Learning Objectives:</div>
                            <div style="white-space: pre-line;">{{ learning_objectives }}</div>
                        </div>
                        {% endif %}
                    </div>

                    <!-- Learning Resources -->
                    {% if learning_plan_links or learning_materials %}
                    <div class="section">
                        <div class="section-title">LEARNING RESOURCES FOR NEXT LEVEL</div>
                        <div class="links-section">
                            {% if learning_plan_links %}
                            <div class="link-item">
                                <strong>Learning Plan Links:</strong><br>
                                <div style="white-space: pre-line;">{{ learning_plan_links }}</div>
                            </div>
                            {% endif %}
                            
                            {% if learning_materials %}
                            <div class="link-item">
                                <strong>Learning Materials:</strong><br>
                                <div style="white-space: pre-line;">{{ learning_materials }}</div>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                    {% endif %}

                    <!-- Next Steps -->
                    <div class="section">
                        <div class="section-title">NEXT STEPS & EXPECTATIONS</div>
                        <div class="instructions">
                            <p><strong>Please proceed with the following actions:</strong></p>
                            <div class="instruction-step">1. <strong>Click "CONTINUE TO TRAINING DASHBOARD"</strong> to access the next level materials</div>
                            <div class="instruction-step">2. Begin the learning materials for the {{ new_level }} level immediately</div>
                            <div class="instruction-step">3. Complete all assigned modules and practical exercises</div>
                            <div class="instruction-step">4. Submit all required assessments on schedule</div>
                            <div class="instruction-step">5. Maintain consistent progress as per training timeline</div>
                            <div class="instruction-step">6. Contact your training coordinator for any clarifications</div>
                        </div>
                    </div>

                    <!-- Performance Expectations -->
                    <div class="section">
                        <div class="section-title">PERFORMANCE EXPECTATIONS</div>
                        <ul>
                            <li>Maintain consistent progress throughout the {{ new_level }} level</li>
                            <li>Achieve minimum passing score of 60% in all assessments</li>
                            <li>Complete all practical assignments satisfactorily</li>
                            <li>Adhere to the training schedule and deadlines</li>
                            <li>Use the dashboard regularly to track your progress</li>
                            <li>Seek assistance promptly if facing any difficulties</li>
                        </ul>
                    </div>

                    <div class="regards">
                        <p>Yours sincerely,</p>
                        <p><strong>Human Resources Department</strong><br>
                        Aligned Automation Solutions Private Limited</p>
                    </div>
                </div>

                <!-- Footer -->
                <div class="footer">
                    <p><strong>CONFIDENTIALITY NOTICE:</strong> This email and any attachments are confidential and intended solely for the use of the individual to whom they are addressed. If you are not the intended recipient, you are strictly prohibited from disclosing, distributing, copying or in any way using this information. Please notify the sender immediately and delete this email from your system.</p>
                    
                    <div class="contact-info">
                        <p><strong>Aligned Automation Solutions Private Limited</strong><br>
                        Email: hr@alignedautomation.com | Phone: +91-XX-XXXX-XXXX<br>
                        Registered Office: [Company Address]</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        template = Template(template_str)
        current_date = datetime.now().strftime("%B %d, %Y")
        return template.render(current_date=current_date, dashboard_url=self.dashboard_url, **context)

# Global email service instance
email_service = EmailService()