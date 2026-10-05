import atexit
import os
import time

from flask import Flask, flash, render_template, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///knee_rehab.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
gpio_module = None


ACTUATOR_RELAY_FORWARD = 17
ACTUATOR_RELAY_REVERSE = 22


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


def _get_gpio():
    global gpio_module

    if gpio_module is None:
        try:
            import RPi.GPIO as gpio
        except ImportError as exc:
            raise RuntimeError(
                'Actuator control is available only on a Raspberry Pi with RPi.GPIO installed.'
            ) from exc

        gpio.setmode(gpio.BCM)
        gpio.setup(ACTUATOR_RELAY_FORWARD, gpio.OUT)
        gpio.setup(ACTUATOR_RELAY_REVERSE, gpio.OUT)
        gpio.output(ACTUATOR_RELAY_FORWARD, gpio.LOW)
        gpio.output(ACTUATOR_RELAY_REVERSE, gpio.HIGH)
        gpio_module = gpio

    return gpio_module


def forward():
    gpio = _get_gpio()
    gpio.output(ACTUATOR_RELAY_FORWARD, gpio.HIGH)
    gpio.output(ACTUATOR_RELAY_REVERSE, gpio.HIGH)


def reverse():
    gpio = _get_gpio()
    gpio.output(ACTUATOR_RELAY_FORWARD, gpio.LOW)
    gpio.output(ACTUATOR_RELAY_REVERSE, gpio.LOW)


def stop():
    gpio = _get_gpio()
    gpio.output(ACTUATOR_RELAY_FORWARD, gpio.LOW)
    gpio.output(ACTUATOR_RELAY_REVERSE, gpio.HIGH)


def cleanup_gpio():
    if gpio_module is not None:
        stop()
        gpio_module.cleanup()


atexit.register(cleanup_gpio)


@app.route('/')
def home():
    return render_template('home.html', active_page='home')


@app.route('/actuator-control')
def actuator_control():
    return render_template('actuator_control.html', active_page='actuator')


@app.route('/forward', methods=['POST'])
def move_forward():
    try:
        forward()
    except RuntimeError as error:
        flash(str(error), 'error')
    else:
        flash('Forward command sent to the actuator.', 'success')
    return redirect(url_for('actuator_control'))


@app.route('/reverse', methods=['POST'])
def move_reverse():
    try:
        reverse()
    except RuntimeError as error:
        flash(str(error), 'error')
    else:
        flash('Reverse command sent to the actuator.', 'success')
    return redirect(url_for('actuator_control'))


@app.route('/stop', methods=['POST'])
def move_stop():
    try:
        stop()
    except RuntimeError as error:
        flash(str(error), 'error')
    else:
        flash('Stop command sent to the actuator.', 'success')
    return redirect(url_for('actuator_control'))


@app.route('/patient', methods=['GET', 'POST'])
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

@app.route('/therapy-control')
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

    session['therapy_patient_id'] = patient.patient_id
    session['therapy_mode'] = therapy_mode
    session['therapy_started_at'] = int(time.time())
    return redirect(url_for('therapy_session'))


@app.route('/therapy-session')
def therapy_session():
    patient_id = session.get('therapy_patient_id')
    therapy_mode = session.get('therapy_mode')
    started_at = session.get('therapy_started_at')

    if not patient_id or therapy_mode not in {'active-assisted', 'passive'} or not started_at:
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        flash('Start a therapy session before opening the session screen.', 'error')
        return redirect(url_for('therapycontrol'))

    patient = Patient.query.filter_by(patient_id=patient_id).first()
    if patient is None:
        session.pop('therapy_patient_id', None)
        session.pop('therapy_mode', None)
        session.pop('therapy_started_at', None)
        flash('The selected patient could not be found. Please start again.', 'error')
        return redirect(url_for('therapycontrol'))

    return render_template(
        'therapy_session.html',
        active_page='therapy-control',
        patient=patient,
        therapy_mode=therapy_mode,
        started_at=started_at
    )


@app.route('/stop-therapy', methods=['POST'])
def stop_therapy():
    session.pop('therapy_patient_id', None)
    session.pop('therapy_mode', None)
    session.pop('therapy_started_at', None)
    flash('The therapy session has been stopped.', 'success')
    return redirect(url_for('therapycontrol'))


@app.route('/progress')
def progress():
    return render_template('progress.html', active_page='progress')

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)