"""Optional: fill an empty database with sample data for a demo.  Run once:  python seed_demo.py
Adds one examination in every phase. Every sample account's password is demo123."""
from datetime import date, datetime, time, timedelta

from app import app
from models import Booking, Course, Examination, ExaminerProfile, Rubric, RubricScore, Slot, StudentProfile, User, db


def day(offset):
    return date.today() + timedelta(days=offset)


def person(role, username, name, **profile):
    user = User(username=username, email=f"{username}@emp.local", full_name=name, role=role)
    user.set_password("demo123")
    if role == "examiner":
        user.examiner_profile = ExaminerProfile(**profile)
    else:
        user.student_profile = StudentProfile(roll_number=profile["roll"], program="BS Data Science")
    db.session.add(user)
    return user.examiner_profile or user.student_profile


def exam(course, name, kind, status, creation, booking, examiners, opened=False):
    """`creation` and `booking` are (first, last) days counted from today."""
    e = Examination(course=course, name=name, exam_type=kind, status=status, duration_minutes=20, max_marks=50,
                    slot_creation_start=day(creation[0]), slot_creation_end=day(creation[1]),
                    booking_start=day(booking[0]), booking_end=day(booking[1]), examiners=examiners,
                    booking_opened_at=datetime.now() if opened else None)
    e.rubrics = [Rubric(criterion_name="Understanding of concepts", max_marks=10, weightage=40),
                 Rubric(criterion_name="Implementation", max_marks=10, weightage=40),
                 Rubric(criterion_name="Communication", max_marks=5, weightage=20)]
    db.session.add(e)
    return e


def slot(e, examiner, offset, hour, capacity, venue, students=(), marks=()):
    """A slot with bookings; marks[i] (if given) evaluates students[i]."""
    s = Slot(examination=e, examiner=examiner, date=day(offset), start_time=time(hour), end_time=time(hour + 1),
             capacity=capacity, available_seats=capacity, venue=venue)
    db.session.add(s)
    for i, student in enumerate(students):
        s.take_seat()
        booking = Booking(student=student, slot=s)
        db.session.add(booking)
        if i < len(marks):
            booking.scores = [RubricScore(rubric=r, marks_awarded=m) for r, m in zip(e.rubrics, marks[i])]
            booking.total_marks = booking.calculate_total()
            booking.remarks, booking.evaluated_at, booking.status = "Good work.", datetime.now(), "Completed"
    return s


with app.app_context():
    if Course.query.first():
        print("The database already has data. Nothing was added.")
    else:
        web = Course(code="CS2003", name="Web Application Development")
        dbms = Course(code="CS2001", name="Database Management Systems")
        py = Course(code="CS1002", name="Programming in Python")
        meera = person("examiner", "meera", "Dr. Meera Iyer", department="Computer Science", status="Approved")
        arjun = person("examiner", "arjun", "Dr. Arjun Rao", department="Computer Science", status="Approved")
        person("examiner", "kavya", "Kavya Menon", department="Data Science", status="Pending")
        asha, rohan, nila, imran = (person("student", u, n, roll=f"23F100010{i}") for i, (u, n) in enumerate(
            [("asha", "Asha Verma"), ("rohan", "Rohan Das"), ("nila", "Nila Krishnan"), ("imran", "Imran Sheikh")], 1))

        exam(py, "Python Practice Assessment", "Assessment", "Draft", (5, 8), (9, 14), [meera])
        exam(dbms, "DBMS Practical", "Practical", "Slot Creation", (-1, 3), (4, 10), [meera, arjun])
        viva = exam(web, "Web App Project Viva", "Viva", "Booking Open", (-6, -1), (-1, 5), [meera, arjun], True)
        slot(viva, meera, 7, 10, 2, "Online - Meet room A", [asha])
        slot(viva, meera, 7, 14, 3, "Online - Meet room A")
        slot(viva, arjun, 8, 11, 1, "Online - Meet room B")
        demo = exam(web, "Web App Project Demo", "Project Demo", "Closed", (-12, -8), (-8, -2), [arjun], True)
        slot(demo, arjun, 0, 15, 3, "Lab 204", [nila, asha, rohan], marks=[(8, 7, 4)])
        mid = exam(py, "Python Mid-term Viva", "Viva", "Completed", (-30, -25), (-25, -20), [meera], True)
        mid.results_published = True
        slot(mid, meera, -15, 10, 3, "Lab 101", [asha, rohan, imran],
             marks=[(9, 8, 5), (6, 7, 3), (7, 9, 4)]).status = "Completed"
        db.session.commit()
        print("Sample data added. Password for every sample account: demo123")
        print("Examiners: meera, arjun (approved), kavya (pending). Students: asha, rohan, nila, imran")
