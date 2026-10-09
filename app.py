import atexit
import importlib
import os
import threading
import time

from functools import wraps
from flask import Flask, flash, jsonify, render_template, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///knee_rehab.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
USERNAME = 'md-kneerehab'
PASSWORD = 'matidevi'
db = SQLAlchemy(app)

try:
    GPIO = importlib.import_module('RPi.GPIO')
except ModuleNotFoundError as error:
    if error.name not in {'RPi', 'RPi.GPIO'}:
        raise
    GPIO = None
    app.logger.warning('Raspberry Pi GPIO is unavailable; actuator movement is disabled.')


class ActuatorController:
    forward_pin = 17
    reverse_pin = 22
    direction_seconds = 20

    def __init__(self, gpio):
        self.gpio = gpio
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.initialized = False
        self.status = 'idle' if gpio is not None else 'unavailable'
        self.direction = 'stopped'

    @property
    def available(self):
        return self.gpio is not None

    def initialize(self):
        if not self.available:
            return

        with self.lock:
            if self.initialized:
                return
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setup(self.forward_pin, self.gpio.OUT, initial=self.gpio.LOW)
            self.gpio.setup(self.reverse_pin, self.gpio.OUT, initial=self.gpio.HIGH)
            self.initialized = True
            self._set_stop_state()

    def _set_stop_state(self):
        self.gpio.output(self.forward_pin, self.gpio.LOW)
        self.gpio.output(self.reverse_pin, self.gpio.HIGH)

    def _set_direction(self, direction):
        self._set_stop_state()
        if direction == 'forward':
            self.gpio.output(self.forward_pin, self.gpio.HIGH)
        else:
            self.gpio.output(self.reverse_pin, self.gpio.LOW)

    def start(self, duration_seconds):
        if not self.available:
            raise RuntimeError('Raspberry Pi GPIO hardware is unavailable.')

        self.initialize()
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError('An actuator session is already running.')

            self._set_stop_state()
            self.stop_event = threading.Event()
            self.status = 'moving'
            self.direction = 'forward'
            started_at = time.time()
            self.thread = threading.Thread(
                target=self._run_cycle,
                args=(duration_seconds, self.stop_event),
                daemon=True
            )
            self.thread.start()
            return started_at

    def _run_cycle(self, duration_seconds, stop_event):
        started = time.monotonic()
        direction = 'forward'
        final_status = 'error'
        try:
            while not stop_event.is_set():
                remaining = duration_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    final_status = 'completed'
                    break

                with self.lock:
                    if stop_event.is_set():
                        final_status = 'stopped'
                        break
                    self._set_direction(direction)
                    self.direction = direction

                if stop_event.wait(min(self.direction_seconds, remaining)):
                    final_status = 'stopped'
                    break
                direction = 'reverse' if direction == 'forward' else 'forward'
            else:
                final_status = 'stopped'
        finally:
            with self.lock:
                self._set_stop_state()
                self.status = final_status
                self.direction = 'stopped'

    def stop(self):
        self.stop_event.set()
        if not self.available or not self.initialized:
            return

        with self.lock:
            self._set_stop_state()
            if self.status == 'moving':
                self.status = 'stopped'
                self.direction = 'stopped'

    def get_state(self):
        with self.lock:
            return self.status, self.direction

    def shutdown(self):
        self.stop()
        if self.available and self.initialized:
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=2)
            if self.thread is not None and self.thread.is_alive():
                app.logger.error('Actuator thread did not stop before GPIO cleanup.')
                return
            self.gpio.cleanup([self.forward_pin, self.reverse_pin])


actuator = ActuatorController(GPIO)
if actuator.available:
    actuator.initialize()
atexit.register(actuator.shutdown)


class Patient(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    patient_id = db.Column(db.String(50), unique=True, nullable=False)

    name = db.Column(db.String(100), nullable=False)

    age = db.Column(db.Integer)

    sex = db.Column(db.String(20))

    phone = db.Column(db.String(30))

    diagnosis = db.Column(db.Text)


with app.app_context():
    db.create_all()


@app.context_processor
def inject_actuator_status():
    return {'actuator_available': actuator.available}


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function

@app.route('/')
@login_required
def home():
    return render_template('home.html', active_page='home')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if username == USERNAME and password == PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('Login successful.', 'success')
            return redirect(url_for('home'))

        flash('Invalid username or password.', 'error')

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/patient', methods=['GET', 'POST'])
@login_required
def patient():

    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip()
        name = request.form.get('name', '').strip()

        if not patient_id or not name:
            flash('Patient ID and full name are required.', 'error')
            return redirect(url_for('patient'))

        if Patient.query.filter_by(patient_id=patient_id).first():
            flash(f'Patient ID {patient_id} already exists. Use a different ID.', 'error')
            return redirect(url_for('patient'))

        new_patient = Patient(
            patient_id=patient_id,
            name=name,
            age=request.form.get('age') or None,
            sex=request.form.get('sex', ''),
            phone=request.form.get('phone', ''),
            diagnosis=request.form.get('diagnosis', '')
        )

        db.session.add(new_patient)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash(f'Patient ID {patient_id} already exists. Use a different ID.', 'error')
            return redirect(url_for('patient'))

        flash('Patient saved successfully.', 'success')
        return redirect(url_for('patient'))

    patients = Patient.query.all()

    return render_template(
        'patient.html',
        active_page='patient',
        patients=patients
    )

@app.route('/patient/<string:patient_id>/edit', methods=['POST'])
@login_required
def edit_patient(patient_id):
    patient = Patient.query.filter_by(patient_id=patient_id).first()
    if patient is None:
        flash('Patient not found. It may have been deleted.', 'error')
        return redirect(url_for('patient'))

    name = request.form.get('name', '').strip()
    age_value = request.form.get('age', '').strip()
    if not name:
        flash('Full name is required.', 'error')
        return redirect(url_for('patient'))

    try:
        age = int(age_value) if age_value else None
    except ValueError:
        flash('Enter a valid age.', 'error')
        return redirect(url_for('patient'))

    if age is not None and age < 0:
        flash('Age cannot be negative.', 'error')
        return redirect(url_for('patient'))

    patient.name = name
    patient.age = age
    patient.sex = request.form.get('sex', '').strip()
    patient.phone = request.form.get('phone', '').strip()
    patient.diagnosis = request.form.get('diagnosis', '').strip()
    db.session.commit()

    flash(f'Patient {patient_id} was updated.', 'success')
    return redirect(url_for('patient'))

@app.route('/patient/<string:patient_id>/delete', methods=['POST'])
@login_required
def delete_patient(patient_id):
    patient = Patient.query.filter_by(patient_id=patient_id).first()
    if patient is None:
        flash('Patient not found. It may already have been deleted.', 'error')
        return redirect(url_for('patient'))

    db.session.delete(patient)
    db.session.commit()

    if session.get('therapy_patient_id') == patient_id:
        actuator.stop()
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        session.pop('therapy_duration_seconds', None)
        session.pop('actuator_status', None)
        session.pop('actuator_direction', None)

    flash(f'Patient {patient_id} was deleted.', 'success')
    return redirect(url_for('patient'))

@app.route('/therapy-control')
@login_required
def therapycontrol():
    if session.get('therapy_patient_id'):
        return redirect(url_for('therapy_session'))

    patients = Patient.query.all()

    return render_template(
        'therapy_control.html',
        active_page='therapy-control',
        patients=patients
    )


@app.route('/start-therapy', methods=['POST'])
@login_required
def start_therapy():
    patient_id = request.form.get('patient_id', '').strip()
    therapy_mode = request.form.get('therapy_mode', '')

    patient = Patient.query.filter_by(patient_id=patient_id).first()
    if patient is None:
        flash('Select a valid patient before starting therapy.', 'error')
        return redirect(url_for('therapycontrol'))

    if therapy_mode not in {'active-assisted', 'passive'}:
        flash('Select a valid therapy type.', 'error')
        return redirect(url_for('therapycontrol'))

    duration_seconds = None
    started_at = int(time.time())
    if therapy_mode == 'passive':
        try:
            duration_minutes = int(request.form.get('duration_minutes', ''))
        except ValueError:
            flash('Choose a valid Passive session duration.', 'error')
            return redirect(url_for('therapycontrol'))

        if not 1 <= duration_minutes <= 60:
            flash('Passive session duration must be between 1 and 60 minutes.', 'error')
            return redirect(url_for('therapycontrol'))

        duration_seconds = duration_minutes * 60
        try:
            started_at = int(actuator.start(duration_seconds))
        except RuntimeError as error:
            flash(str(error), 'error')
            return redirect(url_for('therapycontrol'))

    session['therapy_patient_id'] = patient.patient_id
    session['therapy_mode'] = therapy_mode
    session['therapy_started_at'] = started_at
    if duration_seconds is not None:
        session['therapy_duration_seconds'] = duration_seconds
    if therapy_mode == 'passive':
        session['actuator_status'] = 'moving'
        session['actuator_direction'] = 'forward'
    else:
        session['actuator_status'] = 'idle'
        session['actuator_direction'] = 'forward'
    return redirect(url_for('therapy_session'))


@app.route('/therapy-session')
@login_required
def therapy_session():
    patient_id = session.get('therapy_patient_id')
    therapy_mode = session.get('therapy_mode')
    started_at = session.get('therapy_started_at')
    duration_seconds = session.get('therapy_duration_seconds')
    actuator_status, actuator_direction = actuator.get_state()

    if not patient_id or therapy_mode not in {'active-assisted', 'passive'} or not started_at:
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        session.pop('therapy_duration_seconds', None)
        session.pop('actuator_status', None)
        session.pop('actuator_direction', None)
        flash('Start a therapy session before opening the session screen.', 'error')
        return redirect(url_for('therapycontrol'))

    if therapy_mode == 'passive' and not duration_seconds:
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        session.pop('therapy_duration_seconds', None)
        session.pop('actuator_status', None)
        session.pop('actuator_direction', None)
        flash('The Passive session duration is missing. Please start again.', 'error')
        return redirect(url_for('therapycontrol'))

    patient = Patient.query.filter_by(patient_id=patient_id).first()
    if patient is None:
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        session.pop('therapy_duration_seconds', None)
        session.pop('actuator_status', None)
        session.pop('actuator_direction', None)
        flash('The selected patient could not be found. Please start again.', 'error')
        return redirect(url_for('therapycontrol'))

    return render_template(
        'therapy_session.html',
        active_page='therapy-control',
        patient=patient,
        therapy_mode=therapy_mode,
        started_at=started_at,
        duration_seconds=duration_seconds,
        actuator_status=actuator_status,
        actuator_direction=actuator_direction
    )


@app.route('/actuator-status')
@login_required
def actuator_status_route():
    status, direction = actuator.get_state()
    return jsonify(status=status, direction=direction)


@app.route('/stop-therapy', methods=['POST'])
@login_required
def stop_therapy():
    if session.get('therapy_mode') == 'passive':
        actuator.stop()
    session.pop('therapy_patient_id', None)
    session.pop('therapy_mode', None)
    session.pop('therapy_started_at', None)
    session.pop('therapy_duration_seconds', None)
    session.pop('actuator_status', None)
    session.pop('actuator_direction', None)
    flash('The therapy session has been stopped.', 'success')
    return redirect(url_for('therapycontrol'))


@app.route('/progress')
@login_required
def progress():
    return render_template('progress.html', active_page='progress')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)