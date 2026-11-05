"""Database tables of the Examination Management Portal and the rules tied to them."""
from datetime import date, datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

# Allowed values of the type and status columns (also used to fill dropdowns).
EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]
EXAM_STATUSES = ["Draft", "Slot Creation", "Booking Open", "Closed", "Completed"]
EXAMINER_STATUSES = ["Pending", "Approved", "Rejected", "Deactivated"]
SLOT_STATUSES = ["Available", "Full", "Cancelled", "Completed"]
BOOKING_STATUSES = ["Booked", "Cancelled", "Completed"]


class User(db.Model, UserMixin):
    """Login account. `role` is "admin", "examiner" or "student"."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    examiner_profile = db.relationship("ExaminerProfile", backref="user", uselist=False)
    student_profile = db.relationship("StudentProfile", backref="user", uselist=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)  # never stored as plain text

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ExaminerProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    department = db.Column(db.String(120), nullable=False)
    designation = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Pending")  # one of EXAMINER_STATUSES

    @property
    def name(self):
        return self.user.full_name

    def has_clash(self, day, start, end, ignore_slot_id=None):
        """True if another live slot of this examiner overlaps the given time."""
        return any(s.overlaps(day, start, end) for s in self.slots
                   if s.id != ignore_slot_id and s.status != "Cancelled")


class StudentProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    roll_number = db.Column(db.String(30), unique=True, nullable=False)
    program = db.Column(db.String(120))
    phone = db.Column(db.String(20))

    @property
    def name(self):
        return self.user.full_name


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="Active")  # Active or Inactive


# Many-to-many: which examiners are assigned to which examination.
exam_examiners = db.Table(
    "exam_examiners",
    db.Column("exam_id", db.Integer, db.ForeignKey("examination.id"), primary_key=True),
    db.Column("examiner_id", db.Integer, db.ForeignKey("examiner_profile.id"), primary_key=True),
)


class Examination(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    exam_type = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, nullable=False)
    max_marks = db.Column(db.Float, nullable=False)
    slot_creation_start = db.Column(db.Date, nullable=False)  # both ends of each period are included
    slot_creation_end = db.Column(db.Date, nullable=False)
    booking_start = db.Column(db.Date, nullable=False)
    booking_end = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="Draft")  # one of EXAM_STATUSES
    booking_opened_at = db.Column(db.DateTime)  # set the first time booking opens; slots lock after that
    results_published = db.Column(db.Boolean, default=False)

    course = db.relationship("Course", backref="examinations")
    examiners = db.relationship("ExaminerProfile", secondary=exam_examiners, backref="examinations")
    rubrics = db.relationship("Rubric", backref="examination", cascade="all, delete-orphan",
                              order_by="Rubric.id")
    slots = db.relationship("Slot", backref="examination", cascade="all, delete-orphan",
                            order_by="Slot.date, Slot.start_time")

    @property
    def slot_creation_open(self):
        """Admin has opened slot creation AND today is inside its dates."""
        return self.status == "Slot Creation" and self.slot_creation_start <= date.today() <= self.slot_creation_end

    @property
    def booking_open(self):
        """Admin has opened booking AND today is inside the booking dates."""
        return self.status == "Booking Open" and self.booking_start <= date.today() <= self.booking_end

    @property
    def booking_started(self):
        return self.booking_opened_at is not None

    @property
    def slots_editable(self):
        return not self.booking_started and self.status != "Completed"

    @property
    def evaluation_open(self):
        """Examiners evaluate after booking closes and before results are published."""
        return self.booking_started and self.status in ("Closed", "Completed") and not self.results_published

    @property
    def total_weightage(self):
        return sum(r.weightage for r in self.rubrics)

    @property
    def bookings(self):
        return [b for s in self.slots for b in s.bookings]

    @property
    def active_booking_count(self):
        return sum(b.status != "Cancelled" for b in self.bookings)

    @property
    def has_evaluations(self):
        return any(b.scores for b in self.bookings)

    @property
    def open_slot_count(self):
        return sum(s.is_bookable for s in self.slots)


class Rubric(db.Model):
    """One criterion. Marked out of `max_marks`; worth `weightage` percent of the final score."""
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("examination.id"), nullable=False)
    criterion_name = db.Column(db.String(120), nullable=False)
    max_marks = db.Column(db.Float, nullable=False)
    weightage = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)


class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("examination.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("examiner_profile.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    venue = db.Column(db.String(120))
    capacity = db.Column(db.Integer, nullable=False)
    available_seats = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="Available")  # one of SLOT_STATUSES
    examiner = db.relationship("ExaminerProfile", backref="slots")

    @property
    def active_bookings(self):
        return [b for b in self.bookings if b.status != "Cancelled"]

    @property
    def is_bookable(self):
        return self.status == "Available" and self.date >= date.today()

    def take_seat(self):
        """Reserve a seat. The slot closes itself when the last seat goes."""
        self.available_seats -= 1
        if self.available_seats == 0:
            self.status = "Full"

    def release_seat(self):
        self.available_seats += 1
        if self.status == "Full":
            self.status = "Available"

    def overlaps(self, day, start, end):
        return self.date == day and self.start_time < end and start < self.end_time


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student_profile.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.id"), nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default="Booked")  # one of BOOKING_STATUSES
    cancelled_at = db.Column(db.DateTime)
    note = db.Column(db.String(255))  # e.g. "Rescheduled by Admin ..."
    total_marks = db.Column(db.Float)  # the fields below are filled in by the examiner
    remarks = db.Column(db.Text)
    evaluated_at = db.Column(db.DateTime)

    student = db.relationship("StudentProfile", backref="bookings")
    slot = db.relationship("Slot", backref="bookings")
    scores = db.relationship("RubricScore", backref="booking", cascade="all, delete-orphan",
                             order_by="RubricScore.rubric_id")

    @property
    def examination(self):
        return self.slot.examination

    @property
    def is_evaluated(self):
        return self.evaluated_at is not None

    @property
    def result_visible(self):
        return self.is_evaluated and self.examination.results_published

    def calculate_total(self):
        """Weighted score out of the examination's maximum marks:
        sum(marks / criterion max x weightage) / total weightage x exam max."""
        exam = self.examination
        earned = sum(s.marks_awarded / s.rubric.max_marks * s.rubric.weightage for s in self.scores)
        return round(earned / exam.total_weightage * exam.max_marks, 2) if exam.total_weightage else 0.0


class RubricScore(db.Model):
    """Marks one booking received for one rubric criterion."""
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey("rubric.id"), nullable=False)
    marks_awarded = db.Column(db.Float, nullable=False)
    comment = db.Column(db.String(255))
    rubric = db.relationship("Rubric")
