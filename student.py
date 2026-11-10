"""Student pages: find examinations, book and cancel slots, see schedule and results."""
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from auth import arg, profile_page, role_required
from models import EXAM_TYPES, Booking, Course, Examination, Slot, db

student_bp = Blueprint("student", __name__, url_prefix="/student")
student_only = role_required("student")


def me():
    return current_user.student_profile


def visible_exams():
    """Students never see drafts or examinations of inactive courses."""
    return Examination.query.join(Course).filter(Examination.status != "Draft", Course.status == "Active")


def booking_for(exam):
    """My booking for this examination that is not cancelled, or None."""
    return next((b for b in me().bookings if b.examination == exam and b.status != "Cancelled"), None)


def booked_exam_ids():
    return [b.slot.exam_id for b in me().bookings if b.status != "Cancelled"]


def own_booking(booking_id):
    booking = next((b for b in me().bookings if b.id == booking_id), None)
    if booking is None:
        abort(403)
    return booking


def by_slot_time(bookings):
    return sorted(bookings, key=lambda b: (b.slot.date, b.slot.start_time))


@student_bp.route("/dashboard")
@student_only
def dashboard():
    open_exams = [e for e in visible_exams() if e.booking_open]
    booked = by_slot_time(b for b in me().bookings if b.status == "Booked")
    results = sorted((b for b in me().bookings if b.result_visible), key=lambda b: b.evaluated_at, reverse=True)
    return render_template("student/dashboard.html", open_exams=open_exams, booked=booked, results=results,
                           open_slot_count=sum(e.open_slot_count for e in open_exams),
                           upcoming=[b for b in booked if b.slot.date >= date.today()],
                           booked_exam_ids=booked_exam_ids())


@student_bp.route("/exams")
@student_only
def exams():
    q, exam_type, course_id = arg("q"), arg("exam_type"), request.args.get("course_id", type=int)
    only_open = request.args.get("only_open") == "1"
    query = visible_exams()
    if q:
        query = query.filter(or_(Examination.name.ilike(f"%{q}%"), Course.code.ilike(f"%{q}%"),
                                 Course.name.ilike(f"%{q}%")))
    if exam_type:
        query = query.filter(Examination.exam_type == exam_type)
    if course_id:
        query = query.filter(Examination.course_id == course_id)
    rows = [e for e in query.order_by(Examination.booking_start.desc()) if e.booking_open or not only_open]
    return render_template("student/exams.html", exams=rows, exam_types=EXAM_TYPES, q=q, exam_type=exam_type,
                           courses=Course.query.filter_by(status="Active").order_by(Course.code).all(),
                           course_id=course_id, only_open=only_open, booked_exam_ids=booked_exam_ids())


@student_bp.route("/exams/<int:exam_id>")
@student_only
def exam_detail(exam_id):
    exam = visible_exams().filter(Examination.id == exam_id).first_or_404()
    return render_template("student/exam_detail.html", exam=exam, my_booking=booking_for(exam),
                           slots=[s for s in exam.slots if s.status != "Cancelled"])


@student_bp.route("/slots/<int:slot_id>/book", methods=["POST"])
@student_only
def book_slot(slot_id):
    slot = db.get_or_404(Slot, slot_id)
    exam = slot.examination
    clash = next((b for b in me().bookings if b.status == "Booked"
                  and b.slot.overlaps(slot.date, slot.start_time, slot.end_time)), None)
    if not exam.booking_open:  # core rule: book only during the booking period
        flash("Booking is not open for this examination.", "danger")
    elif booking_for(exam):  # core rule: no duplicate booking for one examination
        flash("You already have a booking for this examination.", "danger")
    elif not slot.is_bookable:  # core rule: never go past the slot's capacity
        flash("That slot is full or no longer available. Choose another one.", "danger")
    elif clash:
        flash(f"This slot overlaps your booking for {clash.examination.name}. Choose another time.", "danger")
    else:
        slot.take_seat()  # marks the slot Full when the last seat goes
        db.session.add(Booking(student=me(), slot=slot))
        db.session.commit()
        flash("Slot booked. It now appears in your schedule.", "success")
        return redirect(url_for("student.bookings"))
    return redirect(url_for("student.exam_detail", exam_id=exam.id))


@student_bp.route("/bookings")
@student_only
def bookings():
    """Complete booking history, including cancelled and completed bookings."""
    return render_template("student/bookings.html",
                           bookings=sorted(me().bookings, key=lambda b: b.booking_date, reverse=True))


@student_bp.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
@student_only
def cancel_booking(booking_id):
    booking = own_booking(booking_id)
    if booking.status != "Booked":
        flash("Only an active booking can be cancelled.", "danger")
    elif not booking.examination.booking_open:  # the deadline passed, or the Admin closed booking
        flash("The booking deadline has passed, so this booking can no longer be cancelled.", "danger")
    else:
        booking.status, booking.cancelled_at = "Cancelled", datetime.now()
        booking.slot.release_seat()  # the seat becomes free for others
        db.session.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("student.bookings"))


@student_bp.route("/schedule")
@student_only
def schedule():
    upcoming = [b for b in me().bookings if b.status == "Booked" and b.slot.date >= date.today()]
    return render_template("student/schedule.html", bookings=by_slot_time(upcoming))


@student_bp.route("/results")
@student_only
def results():
    published = sorted((b for b in me().bookings if b.result_visible), key=lambda b: b.evaluated_at, reverse=True)
    awaiting = [b for b in me().bookings if b.status != "Cancelled" and not b.result_visible]
    return render_template("student/results.html", results=published, awaiting=awaiting)


@student_bp.route("/results/<int:booking_id>")
@student_only
def result_detail(booking_id):
    booking = own_booking(booking_id)
    if not booking.result_visible:
        flash("This result has not been published yet.", "info")
        return redirect(url_for("student.results"))
    return render_template("student/result_detail.html", booking=booking)


@student_bp.route("/results/<int:booking_id>/scorecard")
@student_only
def scorecard(booking_id):
    """The scorecard as a self-contained HTML file to download."""
    booking = own_booking(booking_id)
    if not booking.result_visible:
        abort(404)
    html = render_template("student/scorecard.html", booking=booking, generated=datetime.now())
    return html, {"Content-Type": "text/html; charset=utf-8",
                  "Content-Disposition": f"attachment; filename=scorecard_{booking.id}.html"}


@student_bp.route("/profile", methods=["GET", "POST"])
@student_only
def profile():
    return profile_page("student/profile.html", me(), ("program",))
