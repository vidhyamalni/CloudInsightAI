"""CloudInsight AI — database models (SQLAlchemy / Flask-Login)."""
import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Account lock mechanism
    failed_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

    @property
    def is_locked(self):
        return bool(self.locked_until and datetime.utcnow() < self.locked_until)


class Analysis(db.Model):
    __tablename__ = "analyses"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    company_name = db.Column(db.String(160), nullable=False)
    provider = db.Column(db.String(60), nullable=False)
    industry = db.Column(db.String(60))
    region = db.Column(db.String(60))
    monthly_budget = db.Column(db.Float, default=0)
    storage_gb = db.Column(db.Integer, default=0)
    expected_users = db.Column(db.Integer, default=0)
    compute_req = db.Column(db.String(40))
    data_sensitivity = db.Column(db.String(40))
    availability_req = db.Column(db.String(40))
    compliance_req = db.Column(db.String(40))
    result_json = db.Column(db.Text)     # full engine output
    ai_insights = db.Column(db.Text)     # cached Gemini JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref=db.backref("analyses", lazy="dynamic"))

    def data(self):
        return json.loads(self.result_json or "{}")

    # Derived display fields (no new columns, so existing databases need no migration).
    @property
    def has_report(self):
        return bool(self.result_json)

    @property
    def status(self):
        return "Completed" if self.has_report else "Incomplete"

    @property
    def title(self):
        return f"Cloud Analysis — {self.company_name}"


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    analysis_id = db.Column(db.Integer, db.ForeignKey("analyses.id"))
    role = db.Column(db.String(16))      # "user" | "assistant"
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120))
    email = db.Column(db.String(255))
    message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)