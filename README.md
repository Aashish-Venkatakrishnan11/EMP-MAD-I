# Examination Management Portal

A multi-role web application in which an Admin sets up examinations, examiners
publish slots, students book them, and marks are awarded against a rubric.

Built only with the frameworks the project statement allows:

| Layer     | Used                                                    |
|-----------|---------------------------------------------------------|
| Back end  | Flask, Flask-SQLAlchemy, Flask-Login                    |
| Front end | Jinja2 templates, HTML, CSS, Bootstrap 5 (bundled file) |
| Database  | SQLite, created by code on first run                    |

No JavaScript is used anywhere. Bootstrap's CSS file is stored in
`static/css`, so the application runs without an internet connection.

## Run it

```
python -m venv venv
venv\Scripts\activate          (Windows)
source venv/bin/activate       (Linux / macOS)
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

The first run creates `instance/emp.sqlite3`, all tables, and the Admin
account. Nothing is created by hand.

| Role  | Username | Password |
|-------|----------|----------|
| Admin | admin    | admin123 |

Examiners and students register from the login page. To start again from an
empty database, stop the app and delete the `instance` folder.

### Optional sample data

```
python seed_demo.py
```

Adds courses, examiners, students and one examination in each phase. Every
sample account uses the password `demo123` (examiners `meera`, `arjun`,
`kavya`; students `asha`, `rohan`, `nila`, `imran`).

## Folder structure

```
examination_management_portal/
    app.py           creates the app, the database, the Admin; JSON API
    models.py        all tables and the rules attached to them
    auth.py          login, logout, registration, helpers shared by every role
    admin.py         Admin pages
    examiner.py      examiner pages
    student.py       student pages
    seed_demo.py     optional sample data
    requirements.txt
    templates/       Jinja2 templates, one folder per role
    static/css/      bootstrap.min.css and style.css
```

## Code tour (read in this order)

1. `models.py`: the tables. Properties such as `Examination.booking_open`
   hold the timeline rules, so every page asks the same question the same way.
2. `auth.py`: `role_required` protects every page; `problems()` turns
   `(failed?, message)` pairs into the list of errors a form shows.
3. `admin.py`, `examiner.py`, `student.py`: one function per page. Add and
   edit pages share one function: two routes, and the ID is only present
   when editing.
4. `app.py`: wires it together and creates the database on first run.

## Life of an examination

1. Admin creates a course, then an examination (status **Draft**) with its
   timeline, adds rubric criteria and assigns approved examiners.
2. Admin opens slot creation (**Slot Creation**). Assigned examiners create,
   update or remove slots while today is inside the slot creation dates.
3. Admin opens slot booking (**Booking Open**). Students book one slot each
   while today is inside the booking dates, and may cancel until the deadline.
   From this moment examiners can no longer change slots.
4. Admin closes slot booking (**Closed**). Examiners evaluate the students in
   their own slots using the rubric and submit marks and remarks.
5. Admin publishes results (**Completed**). Students see marks, feedback and
   can download a scorecard.

## Where each core rule lives

| Rule in the project statement                       | Code                                                     |
|-----------------------------------------------------|----------------------------------------------------------|
| Admin pre-exists, no Admin registration             | the `with app.app_context()` block in `app.py`           |
| Examiner needs approval to reach the dashboard      | `role_required` in `auth.py`                             |
| Slots only during the slot creation period          | `Examination.slot_creation_open`, `slot_form` (examiner) |
| Update or remove slots only before booking starts   | `Examination.slots_editable`, `slot_form`, `slot_delete` |
| Book only during the booking period                 | `Examination.booking_open`, `book_slot` (student)        |
| No overbooking, slot closes itself when full        | `Slot.is_bookable`, `Slot.take_seat`                     |
| No duplicate booking for the same examination       | `booking_for` in `student.py`                            |
| Cancel only before the booking deadline             | `cancel_booking` (student), `Slot.release_seat`          |
| Examiners evaluate only students in their own slots | ownership check at the top of `evaluate` (examiner)      |
| Complete booking history                            | bookings are never deleted, only given a new status      |
| Admin reschedules a booking                         | `booking_reschedule` in `admin.py`                       |
| Admin changes the examiner of a slot                | `slot_change_examiner` in `admin.py`                     |
| Search by name or ID                                | `matching` and the `find_*` helpers in `admin.py`        |

### How the final score is calculated

Each criterion has its own maximum and a weightage in percent.

```
final = sum(marks / criterion maximum x weightage) / total weightage x examination maximum
```

Example: maximum 50, criteria 10 marks at 60% and 5 marks at 40%. Marks of
8 and 4 give (0.8 x 60 + 0.8 x 40) / 100 x 50 = 40. See
`Booking.calculate_total` in `models.py`.

## Database tables

| Table             | Purpose                                 | Relations                                         |
|-------------------|-----------------------------------------|---------------------------------------------------|
| user              | login account and role                  | one-to-one with examiner_profile, student_profile |
| examiner_profile  | department, contact, approval status    | belongs to user; has many slot                    |
| student_profile   | roll number, programme, contact         | belongs to user; has many booking                 |
| course            | course code, name, status               | has many examination                              |
| examination       | type, duration, marks, timeline, status | belongs to course; has many rubric, slot          |
| exam_examiners    | examiners assigned to examinations      | many-to-many: examination and examiner_profile    |
| rubric            | criterion, maximum marks, weightage     | belongs to examination                            |
| slot              | date, time, capacity, seats, status     | belongs to examination and examiner_profile       |
| booking           | status, total marks, remarks            | belongs to student_profile and slot               |
| rubric_score      | marks for one criterion of one booking  | belongs to booking and rubric                     |

## API endpoints

All are read-only, return JSON and need a logged-in session.

| Method | Endpoint             | Who                                                     |
|--------|----------------------|---------------------------------------------------------|
| GET    | `/api/examinations`  | everyone (students do not see drafts); includes slots   |
| GET    | `/api/bookings`      | admin: all; examiner: own slots; student: own bookings  |
| GET    | `/api/examiners`     | admin                                                   |
| GET    | `/api/students`      | admin                                                   |
| GET    | `/api/stats`         | admin                                                   |

## Optional features included

- Role-based access control with Flask-Login
- JSON API
- HTML5 validation in forms and matching validation in the controllers
- Statistics on the Admin dashboard, drawn with Bootstrap progress bars
- Downloadable scorecard
- Examiner clash check, student clash check, cancelling a slot as Admin
